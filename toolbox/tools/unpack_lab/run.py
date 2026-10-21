"""Bounded stage driver for the self-built benign unpacking corpus."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import capstone
import httpx
import pefile

ROOT = Path(__file__).resolve().parents[3] / 'lab' / 'unpack'
RUNS = ROOT / 'runs'
OWNER = RUNS / 'debugger-owner.json'
API = 'http://127.0.0.1:27042'
MARKER = 'AGG_UNPACK_LAB_OK:v1'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
    temporary.replace(path)


@contextmanager
def locked():
    import msvcrt
    RUNS.mkdir(parents=True, exist_ok=True)
    with (RUNS / 'stage.lock').open('a+b') as handle:
        handle.seek(0, 2)
        if not handle.tell():
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError('Another lab stage is using the debugger; retry later') from exc
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def api(route, payload=None, **query):
    with httpx.Client(base_url=API, timeout=8, trust_env=False, verify=False) as client:
        response = client.get(route, params=query) if payload is None else client.post(route, json=payload)
        response.raise_for_status()
        result = response.json()
    if not result.get('success'):
        raise RuntimeError(f'{route}: {result}')
    data = result.get('data', {})
    if isinstance(data, dict) and data.get('success') is False:
        raise RuntimeError(f'{route}: {data}')
    return data


def command(value):
    return api('/api/command/exec', {'command': value})


def memory(address, size):
    result = bytearray()
    while len(result) < size:
        count = min(2048, size-len(result))
        data = api('/api/memory/read', address=hex(address+len(result)), size=str(count))
        chunk = bytes.fromhex(data['hex'])
        if len(chunk) != count:
            raise RuntimeError('Short debugger memory read')
        result.extend(chunk)
    return bytes(result)


def cip():
    return int(api('/api/registers/get', name='cip')['value'], 16)


def target(state):
    record = load(ROOT / 'registry.json').get(state['sample'])
    path = Path(record['file']).resolve() if record else None
    if not path or path.parent != (ROOT / 'corpus').resolve() or sha(path.read_bytes()) != record['sha256']:
        raise RuntimeError('Sample is not in the registered corpus or its hash changed')
    return path


def decoder():
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    return md


def inspect(path):
    pe = pefile.PE(str(path))
    ep = pe.OPTIONAL_HEADER.AddressOfEntryPoint
    base = pe.OPTIONAL_HEADER.ImageBase
    section = pe.get_section_by_rva(ep)
    if not section or pe.OPTIONAL_HEADER.Magic != 0x20b:
        raise RuntimeError('Only valid PE64 corpus fixtures are supported')
    code = list(decoder().disasm(section.get_data()[ep-section.VirtualAddress:], base+ep))
    transfers = []
    for inst in code:
        if inst.mnemonic == 'jmp' and inst.operands[0].type == capstone.CS_OP_IMM:
            destination = inst.operands[0].imm-base
            dest_section = pe.get_section_by_rva(destination)
            if dest_section and dest_section != section and dest_section.Characteristics & 0x20000000:
                transfers.append({'at_rva': inst.address-base, 'target_rva': destination})
    imports = {item.name.decode(): item.address-base
               for lib in getattr(pe, 'DIRECTORY_ENTRY_IMPORT', []) for item in lib.imports if item.name}
    peb = any(inst.mnemonic == 'cmp' and '[rbx + 2]' in inst.op_str for inst in code)
    debugger_iat = imports.get('IsDebuggerPresent')
    api_check = debugger_iat is not None and any(
        inst.mnemonic == 'mov' and inst.op_str == f'eax, {hex(debugger_iat)}' for inst in code)
    return {'ep_rva': ep, 'image_base': base, 'upx_signature': b'UPX!' in path.read_bytes(),
            'sections': [{'name': s.Name.decode(errors='replace').strip('\0'),
                          'rva': s.VirtualAddress, 'virtual_size': s.Misc_VirtualSize,
                          'raw_size': s.SizeOfRawData, 'executable': bool(s.Characteristics & 0x20000000),
                          'entropy': round(s.get_entropy(), 3)} for s in pe.sections],
            'transfers': transfers, 'anti_debug': {'peb_check': peb, 'api_check': api_check},
            'ep_instructions': [f'{inst.address-base:x}: {inst.mnemonic} {inst.op_str}' for inst in code[:24]],
            'supported_routes': ['dynamic', 'passthrough']}


def parse_object(text):
    text = text.strip()
    if text.startswith('```') and text.endswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text)[:-3].strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object')
    return value


def parse_plan(text):
    value = parse_object(text)
    if not isinstance(value, dict) or value.get('route') not in ('dynamic', 'passthrough'):
        raise ValueError('Plan needs route=dynamic or passthrough')
    if type(value.get('hide_debugger')) is not bool:
        raise ValueError('Plan needs boolean hide_debugger')
    return value


def check_owner(state):
    if not OWNER.exists() or load(OWNER).get('run_id') != state['run_id']:
        raise RuntimeError('This run does not own the debugger')
    info = api('/api/process/info')
    if info.get('pid') != state.get('pid'):
        raise RuntimeError('Debugger process identity changed')
    mod = api('/api/modules/get', name=target(state).name)
    if int(mod['base'], 16) != state['base']:
        raise RuntimeError('Debugger target module changed')
    if api('/api/debug/state')['state'] != 'paused':
        raise RuntimeError('Debugger must be paused before this stage')


def wait_at(address, previous=None, timeout=12):
    deadline = time.monotonic()+timeout
    time.sleep(0.08)
    while time.monotonic() < deadline:
        status = api('/api/debug/state')
        if status['state'] == 'stopped':
            raise RuntimeError('Debuggee exited before the requested breakpoint')
        if status['state'] == 'paused':
            current = cip()
            if current == address:
                return current
            if current != previous:
                raise RuntimeError(f'Unexpected debugger stop at {current:#x}; wanted {address:#x}')
        time.sleep(0.08)
    raise RuntimeError(f'Timed out reaching {address:#x}')


def release(state):
    if not OWNER.exists() or load(OWNER).get('run_id') != state['run_id']:
        return
    info = api('/api/process/info')
    if info.get('pid') == state.get('pid'):
        api('/api/debug/stop', {})
        deadline = time.monotonic()+5
        while api('/api/debug/state')['state'] != 'stopped':
            if time.monotonic() > deadline:
                raise RuntimeError('Debugger stop timed out')
            time.sleep(0.08)
    OWNER.unlink()


def dump(state, path, folder):
    pe = pefile.PE(str(path))
    method = 'runtime_code_with_original_import_metadata'
    if state['evidence']['upx_signature']:
        if len(pe.sections) < 3 or pe.sections[0].SizeOfRawData != 0:
            raise RuntimeError('Unsupported UPX layout')
        # UPX is used to recover file/import metadata; code and OEP come from the live process.
        for index, section in enumerate(pe.sections[:3]):
            section.Name = f'UPX{index}'.encode().ljust(8, b'\0')
        pe.FILE_HEADER.NumberOfSections = 3
        final = pe.sections[2]
        align = pe.OPTIONAL_HEADER.SectionAlignment
        pe.OPTIONAL_HEADER.SizeOfImage = (final.VirtualAddress+max(final.Misc_VirtualSize, final.SizeOfRawData)+align-1)//align*align
        repaired = folder / 'upx-metadata-input.exe'
        template = folder / 'upx-metadata.exe'
        repaired.write_bytes(pe.write())
        process = subprocess.run([str(ROOT.parents[1] / 'toolbox/tools/upx/bin/upx.exe'),
                                  '-d', '-o', str(template), str(repaired)],
                                 capture_output=True, timeout=20)
        if process.returncode:
            raise RuntimeError('UPX metadata recovery failed: '+process.stderr.decode(errors='replace')[:200])
        pe = pefile.PE(str(template))
        method = 'runtime_code_with_upx_recovered_import_metadata'
    base = state['base']
    out = bytearray(pe.write())
    captured = []
    for section in pe.sections:
        if section.Characteristics & 0x20000000 and section.SizeOfRawData:
            data = memory(base+section.VirtualAddress, section.SizeOfRawData)
            start = section.PointerToRawData
            out[start:start+len(data)] = data
            captured.append({'rva': section.VirtualAddress, 'size': len(data), 'sha256': sha(data)})
    rebuilt = pefile.PE(data=bytes(out))
    rebuilt.OPTIONAL_HEADER.AddressOfEntryPoint = state['oep_rva']
    rebuilt.OPTIONAL_HEADER.CheckSum = 0
    rebuilt.OPTIONAL_HEADER.DllCharacteristics &= ~0x40
    output = folder / 'unpacked.exe'
    output.write_bytes(rebuilt.write())
    output_pe = pefile.PE(str(output))
    if output_pe.get_data(state['oep_rva'], 32) != memory(base+state['oep_rva'], 32):
        raise RuntimeError('Dump OEP bytes differ from captured runtime code')
    return {'output': str(output), 'output_sha256': sha(output.read_bytes()),
            'method': method, 'captured_sections': captured, 'oep_rva': state['oep_rva']}


def execute(params):
    action = params['action']
    with locked():
        if action == 'inspect':
            state = {'run_id': uuid.uuid4().hex[:16], 'sample': params['sample'], 'stage': 'inspected'}
            path = target(state)
            state['evidence'] = inspect(path)
            folder = RUNS / state['run_id']
            folder.mkdir()
            save(folder / 'state.json', state)
            return {'run_id': state['run_id'], 'sample': state['sample'], **state['evidence']}
        run_id = params.get('run_id', '')
        if not re.fullmatch(r'[a-f0-9]{16}', run_id):
            raise ValueError('A valid run_id is required')
        folder = RUNS / run_id
        state = load(folder / 'state.json')
        path = target(state)
        required = {'plan': 'inspected', 'ep': 'planned', 'breakpoint': 'ep', 'oep': 'breakpoint',
                    'validate': 'oep', 'dump': 'validated', 'passthrough': 'planned', 'verify': 'dumped',
                    'report': 'verified'}
        if action != 'release' and state['stage'] != required.get(action):
            raise ValueError(f'Invalid stage order: {action} after {state["stage"]}')
        try:
            if action == 'plan':
                plan = parse_plan(params['plan'])
                evidence = state['evidence']
                expected = 'dynamic' if evidence['transfers'] else 'passthrough'
                anti = any(evidence['anti_debug'].values())
                if plan['route'] != expected or plan['hide_debugger'] != anti:
                    raise ValueError('Plan contradicts observed transfer/anti-debug evidence')
                if expected == 'dynamic' and len(evidence['transfers']) != 1:
                    raise ValueError('Ambiguous transfer candidates: this lab driver needs one')
                state['plan'] = plan
                result, next_stage = {'run_id': run_id, 'route': expected, 'plan_valid': True}, 'planned'
            elif action == 'ep':
                if state['plan']['route'] != 'dynamic':
                    raise ValueError('EP stage requires the dynamic route')
                if OWNER.exists() or api('/api/debug/state')['state'] != 'stopped':
                    raise RuntimeError('Debugger is occupied; release its existing lab run first')
                save(OWNER, {'run_id': run_id})
                command(f'init "{path}"')
                deadline = time.monotonic()+8
                while api('/api/debug/state')['state'] != 'paused':
                    if time.monotonic() > deadline:
                        raise RuntimeError('Debugger init timed out')
                    time.sleep(0.08)
                state['pid'] = api('/api/process/info')['pid']
                mod = api('/api/modules/get', name=path.name)
                state['base'] = int(mod['base'], 16)
                save(folder / 'state.json', state)
                check_owner(state)
                if state['plan']['hide_debugger']:
                    api('/api/antidebug/hide_debugger', {})
                address = state['base']+state['evidence']['ep_rva']
                previous = cip()
                if previous != address:
                    command(f'bp {address:x},ss')
                    api('/api/debug/run', {})
                    wait_at(address, previous)
                result, next_stage = {'ep_va': hex(address), 'observed_cip': hex(cip()),
                                      'pid': state['pid'], 'hide_debugger': state['plan']['hide_debugger']}, 'ep'
            elif action == 'breakpoint':
                check_owner(state)
                transfer = state['evidence']['transfers'][0]
                state['transfer'] = transfer
                command(f'bp {state["base"]+transfer["at_rva"]:x},ss')
                result, next_stage = {'breakpoint_va': hex(state['base']+transfer['at_rva']),
                                      'candidate_oep_rva': transfer['target_rva']}, 'breakpoint'
            elif action == 'oep':
                check_owner(state)
                previous = cip()
                api('/api/debug/run', {})
                wait_at(state['base']+state['transfer']['at_rva'], previous)
                previous = cip()
                api('/api/debug/step_into', {})
                current = wait_at(state['base']+state['transfer']['target_rva'], previous)
                state['oep_rva'] = current-state['base']
                result, next_stage = {'oep_va': hex(current), 'oep_rva': state['oep_rva'],
                                      'observed_by': 'breakpoint_then_step_into_cross_section_jump'}, 'oep'
            elif action == 'validate':
                check_owner(state)
                pe = pefile.PE(str(path))
                section = pe.get_section_by_rva(state['oep_rva'])
                address = state['base']+state['oep_rva']
                head = memory(address, 32)
                instructions = list(decoder().disasm(head, address))
                if cip() != address or not section or not section.Characteristics & 0x20000000 or len(instructions) < 3:
                    raise RuntimeError('OEP validation failed')
                if head == pe.get_data(state['oep_rva'], 32):
                    raise RuntimeError('Code did not change after the claimed unpacking transfer')
                result, next_stage = {'valid': True, 'oep_rva': state['oep_rva'], 'code_sha256': sha(head),
                                      'runtime_code_changed': True,
                                      'instructions': [f'{i.mnemonic} {i.op_str}' for i in instructions[:6]]}, 'validated'
            elif action == 'dump':
                check_owner(state)
                result = dump(state, path, folder)
                state['dump'] = result
                release(state)
                next_stage = 'dumped'
            elif action == 'passthrough':
                if state['plan']['route'] != 'passthrough':
                    raise ValueError('Passthrough is only for the unpacked control')
                output = folder / 'unpacked.exe'
                shutil.copy2(path, output)
                state['oep_rva'] = state['evidence']['ep_rva']
                result = {'output': str(output), 'output_sha256': sha(output.read_bytes()),
                          'oep_rva': state['oep_rva'], 'method': 'plain_control_copy'}
                state['dump'] = result
                next_stage = 'dumped'
            elif action == 'verify':
                output = folder / 'unpacked.exe'
                if sha(output.read_bytes()) != state['dump']['output_sha256']:
                    raise RuntimeError('Dump file hash changed before verification')
                process = subprocess.run([str(output)], capture_output=True, timeout=5,
                                         creationflags=subprocess.CREATE_NO_WINDOW)
                marker_ok = process.stdout.strip() == MARKER.encode()
                result = {**state['dump'], 'sample': state['sample'], 'run_id': run_id,
                          'exit_code': process.returncode, 'marker_ok': marker_ok,
                          'verified': process.returncode == 0 and marker_ok,
                          'stdout': process.stdout.decode('utf-8', 'replace').strip()}
                if not result['verified']:
                    raise RuntimeError(f'Independent dump verification failed: {result}')
                state['verification'] = result
                next_stage = 'verified'
            elif action == 'release':
                release(state)
                result = {'released': True, 'run_id': run_id}
                next_stage = state['stage'] if state['stage'] in ('verified', 'failed') else 'released'
            elif action == 'report':
                report = parse_object(params['report'])
                evidence = state['verification']
                for field in ('sample', 'verified', 'oep_rva', 'method'):
                    if type(report.get(field)) is not type(evidence[field]) or report[field] != evidence[field]:
                        raise ValueError(f'Model report contradicts verified field {field}')
                methods = {'plain_control_copy': '无壳对照复制，未执行动态解壳',
                           'runtime_code_with_original_import_metadata': '抓取运行时代码，保留原始导入元数据',
                           'runtime_code_with_upx_recovered_import_metadata': '抓取运行时代码，UPX 辅助恢复导入与文件元数据'}
                text = (f'样本：{report["sample"]}\n验证：通过，退出码 0，输出标记匹配\n'
                        f'OEP RVA：{report["oep_rva"]:#x}\n方法：{methods[report["method"]]}\n'
                        f'产物：{evidence["output"]}')
                result, next_stage = {'report': text, 'model_fields_valid': True,
                                      'model_report': report}, 'verified'
            else:
                raise ValueError('Unknown lab action')
            state['stage'] = next_stage
            state.pop('error', None)
            state.setdefault('history', []).append({'action': action, 'result': result})
            save(folder / 'state.json', state)
            return result
        except Exception as exc:
            if action == 'plan':
                state.setdefault('rejected_plans', []).append({'text': params.get('plan'), 'error': str(exc)})
                state['stage'] = 'inspected'
            else:
                state['stage'] = 'failed'
            state['error'] = str(exc)
            try:
                release(state)
            except Exception as cleanup:
                state['cleanup_error'] = str(cleanup)
            save(folder / 'state.json', state)
            raise


def main(params):
    print(json.dumps(execute(params), ensure_ascii=False))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        main(json.loads(sys.argv[1]))
    except Exception as exc:
        print(json.dumps({'error': str(exc)}))
        sys.exit(1)
