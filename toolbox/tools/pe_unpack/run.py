"""Evidence-bound unpacking of real inputs; uncertain dumps stay unverified."""
import importlib.util
import json
import os
import re
import struct
import subprocess
import sys
import time
import uuid
from contextlib import ExitStack
from pathlib import Path

import capstone
import pefile

TOOLS = Path(__file__).resolve().parents[1]
RUNS = TOOLS.parent / 'workspace' / 'unpack-runs'
UPX = TOOLS / 'upx/bin/upx.exe'
MAX_FILE = 64 * 1024 * 1024
MAX_IMAGE = 32 * 1024 * 1024
TERMINAL = {'invalid_input', 'unsupported', 'needs_manual', 'no_packing_detected',
            'failed', 'cancelled', 'unpacked', 'dumped_unverified'}


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


io = module('pe_unpack_debug_io', TOOLS / 'unpack_lab/run.py')
detect = module('pe_unpack_detector', TOOLS / 'packer_detect/run.py')
sha, load, save = io.sha, io.load, io.save
api, command, memory, cip = io.api, io.command, io.memory, io.cip


def cancelled(state):
    return (RUNS / state['run_id'] / 'cancel').exists()


def checkpoint(state):
    if cancelled(state):
        raise RuntimeError('Run cancelled')


def input_path(state):
    path = RUNS / state['run_id'] / state['snapshot']
    if sha(path.read_bytes()) != state['input_sha256']:
        raise RuntimeError('Input snapshot hash changed')
    return path


def decoder(bits):
    md = capstone.Cs(capstone.CS_ARCH_X86,
                     capstone.CS_MODE_64 if bits == 64 else capstone.CS_MODE_32)
    md.detail = True
    return md


def structure(path):
    data = path.read_bytes()
    pe = pefile.PE(data=data)
    if not 0 < len(pe.sections) <= 96 or pe.OPTIONAL_HEADER.SizeOfImage > MAX_FILE * 4:
        raise ValueError('Invalid or oversized PE image')
    ep = pe.OPTIONAL_HEADER.AddressOfEntryPoint
    section = pe.get_section_by_rva(ep)
    if not section or not section.Characteristics & 0x20000000:
        raise ValueError('Entry point is not in an executable section')
    ranges = []
    for item in pe.sections:
        if item.SizeOfRawData and (item.PointerToRawData < pe.OPTIONAL_HEADER.SizeOfHeaders
                                  or item.PointerToRawData + item.SizeOfRawData > len(data)):
            raise ValueError('Section data is outside the file')
        if item.VirtualAddress + max(item.Misc_VirtualSize, item.SizeOfRawData) > pe.OPTIONAL_HEADER.SizeOfImage:
            raise ValueError('Section is outside SizeOfImage')
        if item.SizeOfRawData:
            region = (item.PointerToRawData, item.PointerToRawData + item.SizeOfRawData)
            if any(region[0] < end and start < region[1] for start, end in ranges):
                raise ValueError('Overlapping raw sections')
            ranges.append(region)
    directory = pe.OPTIONAL_HEADER.DATA_DIRECTORY[1]
    imports = getattr(pe, 'DIRECTORY_ENTRY_IMPORT', [])
    imports_valid = not directory.VirtualAddress or bool(imports)
    for library in imports:
        imports_valid &= bool(library.dll and library.imports)
        for item in library.imports:
            imports_valid &= bool(item.name or item.ordinal is not None)
    if not imports_valid:
        raise ValueError('Import directory is not parseable')
    return {'structural_valid': True, 'architecture': 'x64' if pe.PE_TYPE == 0x20b else 'x86',
            'ep_rva': ep, 'imports_present': bool(imports),
            'import_count': sum(len(library.imports) for library in imports)}


def inspect(path):
    data = path.read_bytes()
    parsed = detect.parse_pe(data)
    die = None
    exe = Path(detect.binary())
    if exe.is_file():
        try:
            proc = subprocess.run([str(exe), '-j', str(path)], capture_output=True,
                                  timeout=12, creationflags=subprocess.CREATE_NO_WINDOW)
            raw = proc.stdout.decode('utf-8', 'replace')
            die = json.loads(raw[raw.index('{'):])
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    evidence = detect.detect(data, parsed, die)
    evidence['die_available'] = die is not None
    evidence['die_signatures'] = [v.get('string', '') for v in detect.die_values(die)][:16]
    if not parsed:
        return {**evidence, 'recommended_route': 'unsupported',
                'reason': 'Input is not a valid Windows PE file'}
    pe = pefile.PE(data=data)
    bits = 64 if pe.PE_TYPE == 0x20b else 32
    ep = pe.OPTIONAL_HEADER.AddressOfEntryPoint
    section = pe.get_section_by_rva(ep)
    if not section:
        raise ValueError('PE entry point is outside the sections')
    instructions = list(decoder(bits).disasm(pe.get_data(ep, min(131072, max(0,
        section.VirtualAddress + section.SizeOfRawData - ep))), ep))
    transfers, memory_writes, decoder_backedge = [], [], False
    for inst in instructions:
        if inst.mnemonic in ('xor', 'rol', 'ror', 'not') and inst.operands and \
                inst.operands[0].type == capstone.CS_OP_MEM and \
                inst.reg_name(inst.operands[0].mem.base) not in ('rsp', 'rbp', 'esp', 'ebp'):
            memory_writes.append(inst.address)
        if inst.mnemonic.startswith('j') and inst.operands and inst.operands[0].type == capstone.CS_OP_IMM:
            decoder_backedge |= any(inst.operands[0].imm <= at < inst.address for at in memory_writes)
        if inst.mnemonic != 'jmp' or not inst.operands:
            continue
        target = inst.operands[0].imm if inst.operands[0].type == capstone.CS_OP_IMM else None
        dest = pe.get_section_by_rva(target) if target is not None else None
        if target is None or (dest and dest.Characteristics & 0x20000000 and
                             (dest != section or target + 128 < ep)):
            transfers.append({'at_rva': inst.address, 'target_rva': target,
                              'instruction': f'{inst.mnemonic} {inst.op_str}'})
    imports = {item.name.decode(errors='replace') for lib in getattr(pe, 'DIRECTORY_ENTRY_IMPORT', [])
               for item in lib.imports if item.name}
    anti = {'peb_check': any('[rbx + 2]' in i.op_str or '[ebx + 2]' in i.op_str for i in instructions),
            'api_check': bool(imports & {'IsDebuggerPresent', 'CheckRemoteDebuggerPresent'})}
    decode_loop = bool(decoder_backedge and transfers)
    verdict = evidence['verdict']
    unsupported = verdict in {'vm_protected', 'dotnet', 'pyinstaller', 'nuitka', 'electron'}
    unsupported |= pe.FILE_HEADER.Machine not in (0x14c, 0x8664)
    unsupported |= bool(pe.FILE_HEADER.Characteristics & 0x2000) or pe.OPTIONAL_HEADER.Subsystem == 1
    if unsupported:
        route, reason = 'unsupported', 'This format/protector needs a dedicated recovery route'
    elif verdict in ('upx', 'upx_modified'):
        route, reason = 'upx', 'UPX evidence: attempt static decompression first'
    elif decode_loop or verdict in ('packed_known', 'packed_suspected'):
        route, reason = 'dynamic', 'Packing or decoder evidence: bounded dynamic OEP discovery'
    else:
        route, reason = 'plain', 'No sufficient packing evidence; this is not proof of absence'
    return {**evidence, 'architecture': 'x64' if bits == 64 else 'x86', 'ep_rva': ep,
            'image_base': pe.OPTIONAL_HEADER.ImageBase, 'image_size': pe.OPTIONAL_HEADER.SizeOfImage,
            'sections': [{'name': s.Name.rstrip(b'\0').decode(errors='replace'),
                          'rva': s.VirtualAddress, 'virtual_size': s.Misc_VirtualSize,
                          'raw_size': s.SizeOfRawData, 'executable': bool(s.Characteristics & 0x20000000),
                          'entropy': round(s.get_entropy(), 3)} for s in pe.sections],
            'transfers': transfers[:24], 'decoder_loop': decode_loop, 'anti_debug': anti,
            'ep_instructions': [f'{i.address:x}: {i.mnemonic} {i.op_str}' for i in instructions[:24]],
            'recommended_route': route, 'reason': reason}


def create_run(params):
    run_id = uuid.uuid4().hex[:16]
    folder = RUNS / run_id
    folder.mkdir(parents=True)
    state = {'run_id': run_id, 'stage': 'inspected', 'status': 'ready', 'artifacts': [],
             'runtime_check': bool(params.get('runtime_check', False)),
             'mode': params.get('mode') or 'auto',
             'timeout_seconds': min(60, max(5, float(params.get('timeout_seconds') or 30))),
             'original': str(params.get('file', '')), 'history': []}
    try:
        raw = state['original'].strip().strip('"')
        path = Path(raw)
        if not path.is_absolute():
            path = TOOLS.parent / 'workspace' / os.environ.get('AGENT_SESSION', 'default') / 'files' / path
        path = path.resolve(strict=True)
        if not path.is_file() or not 0 < path.stat().st_size <= MAX_FILE:
            raise ValueError('Expected a nonempty file of at most 64 MB')
        data = path.read_bytes()
        if len(data) > MAX_FILE:
            raise ValueError('File grew beyond the input limit')
        state.update(original=str(path), input_sha256=sha(data), snapshot='input' + (path.suffix or '.bin'))
        snapshot = folder / state['snapshot']
        snapshot.write_bytes(data)
        state['evidence'] = inspect(snapshot)
        if state['mode'] == 'dynamic' and state['evidence']['recommended_route'] in ('plain', 'upx'):
            state['evidence']['recommended_route'] = 'dynamic'
            state['evidence']['reason'] = 'Operator selected bounded dynamic discovery'
    except Exception as exc:
        state['status'], state['reason'] = 'invalid_input', str(exc)
        state['evidence'] = {'recommended_route': 'unsupported', 'reason': str(exc)}
    save(folder / 'state.json', state)
    return {'run_id': run_id, 'file': state['original'], 'status': state['status'], **state['evidence']}


def plan(state, text):
    recommended = state['evidence']['recommended_route']
    accepted, advisory = False, None
    try:
        advisory = io.parse_object(text)
        accepted = advisory.get('route') == recommended
    except (ValueError, AttributeError):
        pass
    state['plan'] = {'route': recommended, 'model_plan_valid': accepted,
                     'model_advice': advisory, 'reason': state['evidence']['reason'],
                     'hide_debugger': any(state['evidence'].get('anti_debug', {}).values())}
    return state['plan']


def artifact(state, path, kind):
    record = {'name': path.name, 'path': str(path), 'kind': kind,
              'sha256': sha(path.read_bytes()),
              'download_url': f'/api/unpack-runs/{state["run_id"]}/artifacts/{path.name}'}
    state['artifacts'].append(record)
    return record


def align(value, unit):
    if unit <= 0 or unit & (unit - 1):
        raise ValueError('Invalid PE alignment')
    return (value + unit - 1) // unit * unit


def unpack_upx(state, folder):
    source = input_path(state)
    output = folder / 'unpacked.exe'
    attempts = []
    if not UPX.is_file():
        raise RuntimeError('Bundled UPX executable is unavailable')
    candidates = [('original', source)]
    pe = pefile.PE(data=source.read_bytes())
    if len(pe.sections) >= 3 and pe.sections[0].SizeOfRawData == 0 and b'UPX!' in source.read_bytes():
        repaired = pefile.PE(data=source.read_bytes())
        for index, section in enumerate(repaired.sections[:3]):
            section.Name = f'UPX{index}'.encode().ljust(8, b'\0')
        repaired.FILE_HEADER.NumberOfSections = 3
        last = repaired.sections[2]
        repaired.OPTIONAL_HEADER.SizeOfImage = align(last.VirtualAddress + max(last.Misc_VirtualSize,
            last.SizeOfRawData), repaired.OPTIONAL_HEADER.SectionAlignment)
        rescue = folder / 'upx-header-repair.exe'
        rescue.write_bytes(repaired.write())
        candidates.append(('section_header_repair', rescue))
    for method, candidate in candidates:
        checkpoint(state)
        output.unlink(missing_ok=True)
        proc = subprocess.run([str(UPX), '-d', '-o', str(output), str(candidate)],
                              capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        attempts.append({'method': method, 'exit_code': proc.returncode,
                         'message': (proc.stdout + proc.stderr).decode('utf-8', 'replace')[-1500:]})
        if proc.returncode or not output.is_file():
            continue
        # Added source sections are retained as inspection evidence. They are
        # not copied into the recovered image because rewriting the section
        # table can invalidate the loader's file layout; UPX's own output is
        # the authoritative executable image.
        checked = structure(output)
        if sha(output.read_bytes()) == state['input_sha256']:
            raise RuntimeError('UPX output is identical to input')
        state.update(output=output.name, output_sha256=sha(output.read_bytes()), method=f'upx_{method}',
                     oep_rva=checked['ep_rva'], status='recovered_static', upx_attempts=attempts)
        if method == 'section_header_repair' and len(pe.sections) > 3:
            state['warnings'] = ['UPX header recovery omits added sections from the recovered PE; '
                                 'the original and its section evidence remain preserved']
            for index, extra in enumerate(pe.sections[3:], 3):
                preserved = folder / f'added-section-{index}.bin'
                preserved.write_bytes(extra.get_data())
                artifact(state, preserved, 'preserved_added_section')
        artifact(state, output, 'recovered_pe')
        return {'status': state['status'], 'method': state['method'], **checked}
    state['upx_attempts'] = attempts
    state['status'], state['reason'] = 'ready', 'UPX recovery failed; attempting the dynamic route'
    return {'status': 'retry_dynamic', 'attempts': attempts}


def check_owner(state, paused=True):
    checkpoint(state)
    if not io.OWNER.exists() or load(io.OWNER).get('run_id') != state['run_id']:
        raise RuntimeError('This run does not own the debugger')
    if api('/api/process/info').get('pid') != state.get('pid'):
        raise RuntimeError('Debugger target PID changed')
    mod = api('/api/modules/get', name=state['snapshot'])
    if int(mod['base'], 16) != state['base']:
        raise RuntimeError('Debugger target module changed')
    if paused and api('/api/debug/state')['state'] != 'paused':
        raise RuntimeError('Debugger must be paused')


def wait_pause(state, previous=None, wanted=None, timeout=None):
    deadline = time.monotonic() + (timeout or state['timeout_seconds'])
    time.sleep(0.1)
    while time.monotonic() < deadline:
        checkpoint(state)
        status = api('/api/debug/state')['state']
        if status == 'stopped':
            raise RuntimeError('Debuggee exited before OEP discovery')
        if status == 'paused':
            current = cip()
            if (wanted is not None and current == wanted) or (wanted is None and current != previous):
                return current
            if wanted is not None and current != previous:
                raise RuntimeError(f'Unexpected debugger stop at {current:#x}, expected {wanted:#x}')
        time.sleep(0.08)
    raise RuntimeError('Debugger wait exceeded the time limit')


def release(state):
    if not io.OWNER.exists():
        return
    owner = load(io.OWNER)
    if owner.get('run_id') != state['run_id']:
        return
    pid = state.get('pid') or owner.get('pid')
    if pid is None:
        if api('/api/debug/state')['state'] == 'stopped':
            io.OWNER.unlink(missing_ok=True)
            return
        try:
            loaded = api('/api/modules/get', name=state['snapshot'])
            expected = Path(owner['target']).resolve()
            loaded_path = Path(loaded.get('path', '')).resolve()
            if loaded_path != expected:
                return
            pid = api('/api/process/info').get('pid')
        except (KeyError, RuntimeError):
            return
    if api('/api/process/info').get('pid') == pid:
        api('/api/debug/stop', {})
        deadline = time.monotonic() + 5
        while api('/api/debug/state')['state'] != 'stopped':
            if time.monotonic() > deadline:
                raise RuntimeError('Debugger stop exceeded the time limit')
            time.sleep(0.08)
    if io.OWNER.exists() and load(io.OWNER).get('run_id') == state['run_id']:
        io.OWNER.unlink()


def start_debug(state, folder):
    path = input_path(state)
    pe = pefile.PE(data=path.read_bytes())
    if pe.OPTIONAL_HEADER.SizeOfImage > MAX_IMAGE:
        raise RuntimeError('Dynamic image exceeds the 32 MB capture budget')
    if io.OWNER.exists() or api('/api/debug/state')['state'] != 'stopped':
        raise RuntimeError('Debugger is occupied by another run or a manual session')
    save(io.OWNER, {'run_id': state['run_id'], 'kind': 'real_file', 'target': str(path)})
    command(f'init "{path}", "", "{Path(state["original"]).parent}"')
    deadline = time.monotonic() + 10
    while api('/api/debug/state')['state'] != 'paused':
        checkpoint(state)
        if time.monotonic() > deadline:
            raise RuntimeError('Debugger initialization timed out; check x32/x64 architecture')
        time.sleep(0.08)
    state['pid'] = api('/api/process/info')['pid']
    state['base'] = int(api('/api/modules/get', name=path.name)['base'], 16)
    save(io.OWNER, {'run_id': state['run_id'], 'kind': 'real_file', 'pid': state['pid'], 'target': str(path)})
    save(folder / 'state.json', state)
    check_owner(state)
    header = memory(state['base'], 512)
    optional_offset = struct.unpack_from('<I', header, 0x3c)[0] + 24
    if optional_offset + 2 > len(header) or struct.unpack_from('<H', header, optional_offset)[0] != pe.PE_TYPE:
        raise RuntimeError('Debugger architecture does not match the input')
    if state['plan']['hide_debugger']:
        api('/api/antidebug/hide_debugger', {})
    address = state['base'] + state['evidence']['ep_rva']
    previous = cip()
    if previous != address:
        command(f'bp {address:x},ss')
        api('/api/debug/run', {})
        wait_pause(state, previous, address)
    return {'status': 'ready', 'ep_va': hex(address), 'pid': state['pid'],
            'hide_debugger': state['plan']['hide_debugger']}


def candidate(state, address):
    pe = pefile.PE(data=input_path(state).read_bytes())
    rva = address - state['base']
    section = pe.get_section_by_rva(rva)
    if not section or not section.Characteristics & 0x20000000 or rva == state['evidence']['ep_rva']:
        return None
    head = memory(address, 64)
    original = pe.get_data(rva, 64).ljust(64, b'\0')
    instructions = list(decoder(64 if pe.PE_TYPE == 0x20b else 32).disasm(head, address))
    if head == original or head == b'\0' * 64 or len(instructions) < 4:
        return None
    return {'candidate_oep_va': hex(address), 'oep_rva': rva,
            'runtime_code_changed': True, 'code_sha256': sha(head),
            'instructions': [f'{i.mnemonic} {i.op_str}' for i in instructions[:8]],
            'confidence': 'heuristic_requires_output_verification'}


def discover_oep(state):
    check_owner(state)
    deadline = time.monotonic() + state['timeout_seconds']
    transfers = state['evidence']['transfers']
    addresses = {state['base'] + item['at_rva'] for item in transfers}
    observations = []
    for address in addresses:
        command(f'bp {address:x},ss')
    if addresses:
        for _ in range(min(len(addresses), 12)):
            previous = cip()
            api('/api/debug/run', {})
            current = wait_pause(state, previous, timeout=max(0.1, deadline - time.monotonic()))
            if current not in addresses:
                raise RuntimeError(f'Unexpected stop at {current:#x} before a transfer breakpoint')
            addresses.remove(current)
            api('/api/debug/step_into', {})
            target = wait_pause(state, current, timeout=max(0.1, deadline - time.monotonic()))
            proof = candidate(state, target)
            observations.append({'transfer_va': hex(current), 'target_va': hex(target), 'accepted': bool(proof)})
            if proof:
                state['oep_rva'], state['oep_evidence'] = proof['oep_rva'], proof
                state['oep_observations'] = observations
                return {**proof, 'observed_by': 'transfer_breakpoint_then_step', 'observations': observations}
            if not addresses or time.monotonic() >= deadline:
                break
    # The debugger engine handles indirect transfers without implementing CPU emulation.
    pe = pefile.PE(data=input_path(state).read_bytes())
    ep_section = pe.get_section_by_rva(state['evidence']['ep_rva'])
    regions = [s for s in pe.sections if s != ep_section and s.Characteristics & 0x20000000]
    if not regions or time.monotonic() >= deadline:
        raise RuntimeError('No validated OEP candidate within the discovery budget')
    condition = ' || '.join(f'(cip >= {state["base"] + s.VirtualAddress:x} && '
        f'cip < {state["base"] + s.VirtualAddress + max(s.Misc_VirtualSize, s.SizeOfRawData):x})' for s in regions)
    previous = cip()
    api('/api/trace/into', {'condition': condition, 'max_steps': '200000', 'log_text': ''})
    target = wait_pause(state, previous, timeout=max(0.1, deadline - time.monotonic()))
    proof = candidate(state, target)
    if not proof:
        raise RuntimeError('Trace stopped without changed executable code; OEP remains unresolved')
    state['oep_rva'], state['oep_evidence'] = proof['oep_rva'], proof
    return {**proof, 'observed_by': 'bounded_conditional_trace'}


def dump(state, folder):
    check_owner(state)
    pe = pefile.PE(data=input_path(state).read_bytes())
    out = bytearray(pe.write())
    captured = []
    deadline = time.monotonic() + 60
    for section in pe.sections:
        checkpoint(state)
        size = max(section.Misc_VirtualSize, section.SizeOfRawData)
        if not size:
            continue
        raw = bytearray()
        while len(raw) < size:
            checkpoint(state)
            if time.monotonic() > deadline:
                raise RuntimeError('Memory capture exceeded the time budget')
            raw.extend(memory(state['base'] + section.VirtualAddress + len(raw), min(2048, size - len(raw))))
        if section.SizeOfRawData >= len(raw):
            offset = section.PointerToRawData
        else:
            offset = align(len(out), pe.OPTIONAL_HEADER.FileAlignment)
            section.PointerToRawData = offset
            section.SizeOfRawData = align(len(raw), pe.OPTIONAL_HEADER.FileAlignment)
        out.extend(b'\0' * max(0, offset + section.SizeOfRawData - len(out)))
        out[offset:offset + len(raw)] = raw
        captured.append({'rva': section.VirtualAddress, 'size': len(raw), 'sha256': sha(raw)})
    header = pe.write()[:pe.OPTIONAL_HEADER.SizeOfHeaders]
    out[:len(header)] = header
    rebuilt = pefile.PE(data=bytes(out))
    rebuilt.OPTIONAL_HEADER.AddressOfEntryPoint = state['oep_rva']
    rebuilt.OPTIONAL_HEADER.ImageBase = state['base']
    rebuilt.OPTIONAL_HEADER.CheckSum = 0
    rebuilt.OPTIONAL_HEADER.DllCharacteristics &= ~0x40
    rebuilt.OPTIONAL_HEADER.DATA_DIRECTORY[4].VirtualAddress = 0
    rebuilt.OPTIONAL_HEADER.DATA_DIRECTORY[4].Size = 0
    # Restore on-disk thunk values so the Windows loader can resolve named imports.
    source = pefile.PE(data=input_path(state).read_bytes())
    pointer_size = 8 if source.PE_TYPE == 0x20b else 4
    restored = 0
    for library in getattr(source, 'DIRECTORY_ENTRY_IMPORT', []):
        lookup = library.struct.OriginalFirstThunk or library.struct.FirstThunk
        for index in range(len(library.imports) + 1):
            value = source.get_data(lookup + index * pointer_size, pointer_size)
            if len(value) != pointer_size:
                raise RuntimeError('Original import lookup table is truncated')
            if pointer_size == 8:
                written = rebuilt.set_qword_at_rva(library.struct.FirstThunk + index * pointer_size,
                                                   int.from_bytes(value, 'little'))
            else:
                written = rebuilt.set_dword_at_rva(library.struct.FirstThunk + index * pointer_size,
                                                   int.from_bytes(value, 'little'))
            if not written:
                raise RuntimeError('Import thunk lies outside the rebuilt file')
            restored += index < len(library.imports)
    # pefile may re-emit section data during write(), restoring the captured
    # runtime IAT over the original import thunks. Patch the serialized bytes
    # last so the loader sees name/ordinal thunks on disk.
    serialized = bytearray(rebuilt.write())
    serialized_pe = pefile.PE(data=bytes(serialized), fast_load=True)
    for library in getattr(source, 'DIRECTORY_ENTRY_IMPORT', []):
        lookup = library.struct.OriginalFirstThunk or library.struct.FirstThunk
        for index in range(len(library.imports) + 1):
            value = source.get_data(lookup + index * pointer_size, pointer_size)
            if len(value) != pointer_size:
                raise RuntimeError('Original import lookup table is truncated')
            rva = library.struct.FirstThunk + index * pointer_size
            try:
                offset = serialized_pe.get_offset_from_rva(rva)
            except Exception as exc:
                raise RuntimeError('Import thunk lies outside the rebuilt file') from exc
            if offset < 0 or offset + pointer_size > len(serialized):
                raise RuntimeError('Import thunk lies outside the rebuilt file')
            serialized[offset:offset + pointer_size] = value
    output = folder / 'candidate.exe'
    output.write_bytes(bytes(serialized))
    final = pefile.PE(data=output.read_bytes())
    if final.get_data(state['oep_rva'], 64) != memory(state['base'] + state['oep_rva'], 64):
        raise RuntimeError('Dump candidate OEP differs from live code')
    state.update(output=output.name, output_sha256=sha(output.read_bytes()),
                 method='runtime_sections_with_original_named_imports',
                 status='dumped_unverified', import_thunks_restored=restored,
                 captured_sections=captured)
    artifact(state, output, 'candidate_pe')
    release(state)
    return {'status': state['status'], 'output': str(output), 'oep_rva': state['oep_rva'],
            'import_thunks_restored': restored, 'captured_sections': captured}


def smoke(state, output, folder):
    checkpoint(state)
    started = time.monotonic()
    with (folder / 'stdout.txt').open('wb') as stdout, (folder / 'stderr.txt').open('wb') as stderr:
        try:
            proc = subprocess.Popen([str(output)], cwd=str(Path(state['original']).parent),
                                    stdout=stdout, stderr=stderr, creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError as exc:
            return {'attempted': True, 'passed': False, 'launch_error': str(exc), 'timed_out': False,
                    'scope': 'bounded smoke test, not functional equivalence'}
        try:
            while proc.poll() is None and time.monotonic() - started < 8:
                checkpoint(state)
                time.sleep(0.08)
        finally:
            if proc.poll() is None:
                subprocess.run(['C:/Windows/System32/taskkill.exe', '/PID', str(proc.pid), '/T', '/F'],
                               capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
                proc.wait(timeout=5)
    timed_out = time.monotonic() - started >= 8
    return {'attempted': True, 'passed': not timed_out and proc.returncode == 0,
            'exit_code': proc.returncode, 'timed_out': timed_out,
            'stdout': (folder / 'stdout.txt').read_bytes()[:4096].decode('utf-8', 'replace'),
            'stderr': (folder / 'stderr.txt').read_bytes()[:4096].decode('utf-8', 'replace'),
            'scope': 'bounded smoke test, not functional equivalence'}


def verify(state, folder):
    result = {'run_id': state['run_id'], 'file': state['original'], 'status': state['status'],
              'method': state.get('method'), 'oep_rva': state.get('oep_rva'),
              'structural_valid': False, 'runtime_verified': False, 'validation_level': 'none',
              'reason': state.get('reason', ''), 'artifacts': state['artifacts'],
              'warnings': state.get('warnings', [])}
    if state.get('output'):
        output = folder / state['output']
        if sha(output.read_bytes()) != state['output_sha256']:
            raise RuntimeError('Artifact hash changed before verification')
        result.update(structure(output), validation_level='structure')
        if state['status'] == 'recovered_static':
            state['status'] = 'unpacked'
            result['reason'] = 'UPX decompression succeeded and PE structure is valid'
        if state['runtime_check']:
            result['runtime_test'] = smoke(state, output, folder)
            result['runtime_verified'] = result['runtime_test']['passed']
            if result['runtime_verified']:
                state['status'], result['validation_level'] = 'unpacked', 'runtime_smoke'
                result['reason'] = 'Recovered output passed a bounded runtime smoke test'
            else:
                state['status'] = 'needs_manual'
                result['reason'] = 'Runtime smoke verification failed or timed out'
        elif state['status'] == 'dumped_unverified':
            result['reason'] = 'Candidate PE captured; imports/OEP and program behavior need further verification'
        result['output'] = str(output)
    result['status'] = state['status']
    state['verification'] = result
    return result


def report(state, model_text, folder):
    evidence = state['verification']
    model_valid = False
    try:
        proposed = io.parse_object(model_text)
        model_valid = all(type(proposed.get(k)) is type(evidence[k]) and proposed[k] == evidence[k]
                          for k in ('status', 'structural_valid', 'runtime_verified', 'validation_level'))
    except (ValueError, AttributeError):
        pass
    labels = {'unpacked': '已解壳', 'dumped_unverified': '已抓取候选文件，尚未运行验证',
              'no_packing_detected': '未发现充分的加壳迹象', 'unsupported': '当前路线不支持',
              'needs_manual': '需要人工处理', 'invalid_input': '输入文件无效', 'failed': '处理失败',
              'cancelled': '已停止'}
    levels = {'none': '未验证', 'structure': 'PE 结构检查', 'runtime_smoke': 'PE 结构检查及运行冒烟检查'}
    text = f'文件：{state["original"]}\n结果：{labels.get(evidence["status"], evidence["status"])}\n'
    text += f'验证：{levels[evidence["validation_level"]]}\n'
    if evidence['oep_rva'] is not None:
        label = '候选 OEP RVA' if state.get('method', '').startswith('runtime_') else 'OEP RVA'
        text += f'{label}：{evidence["oep_rva"]:#x}\n'
    if evidence['reason']:
        text += f'证据：{evidence["reason"]}\n'
    for warning in evidence.get('warnings', []):
        text += f'限制：{warning}\n'
    for item in evidence['artifacts']:
        text += f'产物：{item["path"]}\n[下载 {item["name"]}]({item["download_url"]})\n'
    if not model_valid:
        text += '模型报告未通过事实核对，以上结果依据工具证据生成。\n'
    state['model_report_valid'] = model_valid
    (folder / 'report.md').write_text(text, encoding='utf-8')
    return {'report': text, 'evidence': evidence, 'model_report_valid': model_valid}


def execute(params):
    action = params['action']
    if action == 'inspect':
        return create_run(params)
    run_id = params.get('run_id', '')
    if not re.fullmatch(r'[a-f0-9]{16}', run_id):
        raise ValueError('A valid run_id is required')
    folder = RUNS / run_id
    state = load(folder / 'state.json')
    if action == 'release':
        # Cancellation must be visible even while another subprocess holds the stage lock.
        (folder / 'cancel').touch()
        release(state)
        return {'released': not (io.OWNER.exists() and load(io.OWNER).get('run_id') == run_id), 'run_id': run_id}
    with ExitStack() as stage_context:
        state = load(folder / 'state.json')
        try:
            if action in ('ep', 'oep', 'validate', 'dump'):
                stage_context.enter_context(io.locked())
            checkpoint(state)
            required = {'plan': {'inspected'}, 'upx': {'plan'}, 'ep': {'plan', 'upx'},
                        'oep': {'ep'}, 'validate': {'oep'}, 'dump': {'validate'},
                        'skip': {'plan'}, 'verify': {'upx', 'dump', 'skip', 'validate', 'oep', 'ep'},
                        'report': {'verify'}}
            if action not in required or (state['status'] not in TERMINAL and
                                          state['stage'] not in required[action]):
                raise ValueError(f'Invalid stage order: {action} after {state["stage"]}')
            if action == 'plan':
                result = plan(state, params.get('plan', ''))
            elif action == 'verify':
                result = verify(state, folder)
            elif action == 'report':
                result = report(state, params.get('report', ''), folder)
            elif state['status'] in TERMINAL:
                result = {'status': state['status'], 'reason': state.get('reason', ''), 'skipped': True}
            else:
                input_path(state)
                if action == 'upx':
                    result = unpack_upx(state, folder)
                elif action == 'ep':
                    result = start_debug(state, folder)
                elif action == 'oep':
                    result = discover_oep(state)
                elif action == 'validate':
                    check_owner(state)
                    result = candidate(state, state['base'] + state['oep_rva'])
                    if not result or result['code_sha256'] != state['oep_evidence']['code_sha256']:
                        raise RuntimeError('OEP evidence changed before capture')
                elif action == 'dump':
                    result = dump(state, folder)
                elif action == 'skip':
                    state['status'] = 'no_packing_detected' if state['plan']['route'] == 'plain' else 'unsupported'
                    state['reason'] = state['evidence']['reason']
                    result = {'status': state['status'], 'reason': state['reason']}
                else:
                    raise ValueError('Unknown stage')
            state['stage'] = action
            state['history'].append({'action': action, 'result': result})
        except Exception as exc:
            state['status'] = 'cancelled' if cancelled(state) else 'needs_manual'
            state['reason'] = f'{action}: {exc}'
            state['stage'] = action
            try:
                release(state)
            except Exception as cleanup:
                state['cleanup_error'] = str(cleanup)
            result = {'run_id': run_id, 'status': state['status'], 'reason': state['reason']}
            if action == 'upx' and not cancelled(state):
                state['status'] = 'ready'
                result['status'] = 'retry_dynamic'
            if action == 'verify':
                state['verification'] = {**result, 'file': state['original'], 'method': state.get('method'),
                    'oep_rva': state.get('oep_rva'), 'structural_valid': False, 'runtime_verified': False,
                    'validation_level': 'none', 'artifacts': state['artifacts']}
                result = state['verification']
            state['history'].append({'action': action, 'result': result})
        save(folder / 'state.json', state)
        return result


def main(params):
    print(json.dumps(execute(params), ensure_ascii=False))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        main(json.loads(sys.argv[1]))
    except Exception as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        sys.exit(1)
