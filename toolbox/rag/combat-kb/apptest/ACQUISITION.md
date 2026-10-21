# Android App Acquisition Workflow

通用 Android 应用获取流程：从发现到本地归档的完整流水线。

## Overview

```
Source (App Store / Web)
    │
    ▼
Category Discovery ───────> Category taxonomy
    │
    ▼
App Listing ───────────────> App list per category
    │
    ▼
Metadata Enrichment ───────> download_url, md5, size, etc.
    │
    ▼
Local Archive ─────────────> info.json per app
    │
    ▼
APK Organization ──────────> Categorized file tree
    │
    ▼
Filename Sanitization ─────> UTF-8 Simplified Chinese names
```

## Stage 1: Category Discovery

Discover the category/sub-category hierarchy from the source.

### Input
- Source website/app store URL

### Method
- Parse the navigation/menu structure
- Extract category ID, name, and sub-category mappings
- Game and app categories are often split

### Output Example

```json
{
  "影音播放": {
    "id": 12,
    "sub": {
      "视频": 1201,
      "音乐": 1202,
      "直播": 1203,
      "铃声": 1204,
      "K歌": 1205,
      "收音机": 1206
    }
  },
  "通讯社交": { ... }
}
```

## Stage 2: App Listing

For each sub-category, paginate through all apps.

### Common API Patterns
- Paginated JSON API returning HTML fragments
- RESTful endpoints: `/api/category/more?catId={id}&subCatId={sid}&page={n}`
- Rate limiting: respect `Retry-After` headers, add delays between requests
- Concurrency: 1-2 concurrent requests per category to avoid bans

### Scraping Strategy

| Component | Approach |
|-----------|----------|
| HTML parsing | Regex on card elements (faster than DOM parser for simple structures) |
| Error handling | Retry 3x on timeout/5xx, skip on 404 |
| Deduplication | Track by `app_id` across all categories |
| Termination | Stop when page returns empty result |

### Output

```
app_list = [
  { "app_id": "7700065", "app_name": "5P医学", "url": "/app/7700065" },
  ...
]
```

## Stage 3: Metadata Enrichment

For each app, fetch detailed metadata including the download URL.

### Download URL Resolution

Most app stores use a redirect chain:

```
1. Click "Download" → /apps/{app_id}/download/dot?ch={channel}
2. Server 302 redirects to CDN URL
3. CDN URL contains: md5={hash}, pkg={packageName}, vcode={versionCode}
```

**Key technique**: Follow the redirect with `requests.get(url, allow_redirects=True)` and extract `md5` from the final URL's query parameters. This avoids needing to parse the download page HTML.

### Metadata Fields

| Field | Source | Method |
|-------|--------|--------|
| `app_id` | App list / URL | Extract from app page URL |
| `app_name` | App list / HTML | Parse page title or card element |
| `package_name` | Download page / APK | Parse page metadata or aapt dump |
| `category` | Stage 1 | From category tree |
| `sub_category` | Stage 1 | From sub-category tree |
| `install_count` | App list HTML | Parse install count text |
| `popularity` | App list HTML | "hot"/"normal"/"cold" badge |
| `size` | App list HTML | File size text |
| `download_url` | CDN redirect | Follow 302 chain |
| `md5` | CDN URL | Extract from `md5=` query param |
| `cdn_params` | CDN URL | All query params as dict |

### Parallel Fetch Strategy

```
ThreadPoolExecutor(max_workers=10)
  for each app:
    fetch download_url (redirect)
    extract md5 from final URL
    write info.json
```

Considerations:
- **Connection pooling**: Reuse sessions with `requests.Session()`
- **Timeouts**: Set connect=10s, read=30s per request
- **Rate limiting**: 10 threads saturates most sources; adjust down if throttled
- **Progress tracking**: Persist completed app IDs to resume on interrupt

## Stage 4: Local Archive

Write structured metadata to disk.

### Directory Layout

```
info/
└── <category>/
    └── <sub_category>/
        └── <AppName> (id_<appId>)/
            └── info.json
```

The `appId` in the directory name guarantees uniqueness even when different apps share the same display name.

### `info.json`

```jsonc
{
  "app_id": "7700065",
  "app_name": "5P医学",
  "package_name": "com.psytap.wpyx",
  "category": "健康运动",
  "sub_category": "养生",
  "install_count": "380人安装",
  "install_count_parsed": 380,
  "popularity": "cold",
  "size": "227.59MB",
  "download_url": "https://cdn.example.com/xxx.apk?md5=...",
  "md5": "12848df71bbf7544800a7e6531078d7e",
  "cdn_params": { "md5": "...", "pkg": "...", ... }
}
```

See [APP_FORMAT.md](./APP_FORMAT.md) for full schema.

## Stage 5: APK Organization

When you have pre-existing APK files (pulled from emulators, devices, etc.), organize them into the category tree.

### Matching Strategy

1. **By directory name**: If `info/<cat>/<sub>/<name> (id_x)/` exists, the APK can go to `apk/<cat>/<sub>/`
2. **By aapt metadata**: Use `aapt dump badging` to extract `package: name=...` and `application-label: ...`
3. **By filename pattern**: If filename already contains the package name, match directly

### File Naming Convention

```
<AppName>_<packageName>_<versionName>.apk
```

### Process

```
for each APK in source:
    pkg_name = aapt_dump_badging(apk)["package"]
    if pkg_name in metadata_db:
        cat, sub = metadata_db[pkg_name]
        dest = f"apk/{cat}/{sub}/{AppName}_{pkg_name}_{version}.apk"
    else:
        dest = f"apk/_未分类/{AppName}_{pkg_name}_{version}.apk"
    move(apk, dest)
```

## Stage 6: Filename Sanitization

APKs pulled from Android emulators often have garbled filenames due to encoding mismatch (GBK ↔ UTF-8).

### Root Cause

Android emulators on Chinese Windows store filenames in GBK encoding. When pulled via `adb pull`, the filename bytes are misinterpreted as UTF-8, producing garbled characters (e.g., `鐧惧害` → `百度`).

### Two-Phase Fix

**Phase A** – Rename using aapt (for APKs whose package is in the metadata DB):

```python
app_name = metadata_db[pkg_name]["app_name"]
safe_name = re.sub(r'[\\/:*?"<>|]', '_', app_name)
new_name = f"{safe_name}_{pkg_name}_{version}.apk"
```

**Phase B** – Rename without metadata (for unknown APKs):

1. Copy the APK to a temp ASCII path (avoids aapt's strict path encoding check)
2. Run `aapt dump badging` on the temp copy
3. Extract `application-label-zh-CN` and `versionName`
4. Rename to `{app_label}_{packageName}_{version}.apk`

```python
shutil.copy2(garbled_path, safe_tmp_path)
result = subprocess.run([aapt, "dump", "badging", safe_tmp_path], ...)
name = re.search(r"application-label(?:-zh-CN)?:[\s]*'([^']*)'", result.stdout)
pkg = re.search(r"package:\s+name='([^']*)'", result.stdout)
ver = re.search(r"versionName='([^']*)'", result.stdout)
new_name = f"{name}_{pkg}_{ver}.apk"
```

**Important**: The regex must handle `application-label:'QQ空间'` (no space after colon). Use `:[\s]*'` instead of `:\s+'`.

## Tools

| Tool | Purpose | Required |
|------|---------|----------|
| Python 3 | Core scripting, HTTP requests, JSON handling | Required |
| `requests` | HTTP client with redirect following | Required |
| `aapt` (build-tools) | Extract APK package name, version, app label | For APK organization |
| Windows (NTFS) | Unicode filenames with Chinese characters | Platform-specific |

## Common Issues & Fixes

| Issue | Symptom | Fix |
|-------|---------|-----|
| Garbled filenames | `鐧惧害鍦板浘_com.baidu.BaiduMap.apk` | Use aapt on temp ASCII copy, extract zh-CN label |
| aapt refuses file | `ziparchive: Unable to open: Illegal byte sequence` | Copy to ASCII path first |
| Empty/zero-byte info files | `os.path.getsize()` returns 0 | Directory mistaken for file; check `os.path.isdir()` |
| Permission denied on info files | `[Errno 13]` on Windows | Check if path encoding is correct; use `-LiteralPath` |
| CDN redirect fails | Timeout or 403 | Verify the download URL hasn't expired; CDN URLs may be time-limited |
