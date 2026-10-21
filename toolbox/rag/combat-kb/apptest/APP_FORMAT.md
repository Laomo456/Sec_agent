# Universal Android APK Archive Format

A standardized structure for organizing Android APK files with associated metadata, designed to be source-agnostic (any app store, manual collection, etc.).

## Directory Layout

```
root/
├── apk/                        # APK binary archive
│   ├── _未分类/                # Uncategorized APKs
│   ├── <category>/
│   │   ├── <sub_category>/
│   │   │   └── <AppName>_<packageName>_<versionName>.apk
│   │   └── ...
│   └── ...
│
└── info/                       # App metadata
    ├── <category>/
    │   ├── <sub_category>/
    │   │   ├── <AppName> (id_<appId>)/
    │   │   │   └── info.json
    │   │   └── ...
    │   └── ...
    └── ...
```

## Conventions

| Element | Format | Example |
|---------|--------|---------|
| AppName | Display name, OS-safe chars only (`\ / : * ? " < > \|` replaced with `_`) | `微信`, `QQ空间`, `rednote` |
| packageName | Android package name (reverse domain) | `com.tencent.mm`, `com.qzone` |
| versionName | Version string from AndroidManifest | `8.0.74`, `8.9.9.288` |
| category | Top-level category in Simplified Chinese | `通讯社交`, `影音播放`, `系统工具` |
| sub_category | Sub-category in Simplified Chinese | `聊天`, `视频`, `安全` |
| appId | Numeric app ID from source | `566489`, `8371738` |

## APK Archive (`apk/`)

### Naming Rule

```
<AppName>_<packageName>_<versionName>.apk
```

Example: `微信_com.tencent.mm_8.0.74.apk`

### Directory Rule

```
<category>/<sub_category>/<AppName>_<packageName>_<versionName>.apk
```

APKs whose category is unknown are placed under `_未分类/` with the same naming rule.

### Category Taxonomy

| Category | Sample Sub-Categories |
|----------|----------------------|
| 通讯社交 | 聊天, 交友, 婚恋, 社区, 私密, 电话通讯 |
| 影音播放 | 视频, 音乐, 直播, K歌, 收音机, 铃声 |
| 系统工具 | 安全, 优化, 浏览器, 输入法, WiFi, Root, 省电, 文件管理 |
| 生活休闲 | 娱乐, 美食, 汽车, 房产家居, 求职, 点餐外卖, 共享服务 |
| 办公商务 | 办公软件, 效率办公, 云盘存储, 笔记, 邮箱 |
| 摄影图像 | 相机, 美化, 图像编辑, 短视频, 相册, 搞怪 |
| 新闻阅读 | 新闻, 小说, 漫画, 电子书, 听书, 搞笑, 资讯 |
| 旅游出行 | 地图导航, 公交地铁, 用车租车, 住宿, 购票, 旅行攻略 |
| 金融理财 | 银行, 支付, 投资, 炒股, 理财记账, 保险, 借贷, 彩票 |
| 健康运动 | 医疗, 减肥健身, 养生, 怀孕, 经期 |
| 网上购物 | 商城, 团购, 快递, 优惠, 全球导购 |
| 考试学习 | 学习, 考试, 英语, 背单词, 翻译, 驾考 |
| 育儿亲子 | 早教, 育儿, 亲子互动, 讲故事, 唱儿歌, 小儿百科 |
| 手机美化 | 壁纸, 主题, 桌面, 字体, 动态壁纸, 锁屏, 桌面部件 |
| 辅助工具 | 修改器 |
| 游戏 | (gaming sub-categories) |
| 角色扮演 | 武侠, 魔幻, 修仙, 回合, 即时, 日韩系 |
| 动作冒险 | 冒险, 格斗, 街机, 跳跃, 横版 |
| 网络游戏 | RPG, 卡牌, 仙侠, 西游, 三国, 竞技策略, 动作射击, 大型 |
| 跑酷竞速 | 跑酷, 赛车, 竞速, 摩托, 躲避 |
| 飞行射击 | 射击, 飞行, 空战, 坦克, 狙击 |
| 策略游戏 | 策略, 经营, 模拟, 塔防, 战争, 养成, 历史 |
| 扑克棋牌 | 斗地主, 麻将, 纸牌, 棋类, 桌游, 单机 |
| 休闲益智 | 休闲, 益智, 消除, 解谜, 音乐, 捕鱼 |
| 体育竞技 | 运动, 篮球, 足球, 桌球, 其他球类 |

## Metadata (`info/`)

### Directory Rule

```
<category>/<sub_category>/<AppName> (id_<appId>)/
    info.json
```

The app ID in the directory name ensures uniqueness even when multiple apps share the same display name.

### `info.json` Schema

```jsonc
{
  // Unique app ID from source
  "app_id": "7700065",

  // Display name (UTF-8)
  "app_name": "5P医学",

  // Android package name
  "package_name": "com.psytap.wpyx",

  // Category hierarchy
  "category": "健康运动",
  "sub_category": "养生",

  // Install count (raw display string + parsed integer)
  "install_count": "380人安装",
  "install_count_parsed": 380,

  // Popularity flag: "hot" | "normal" | "cold"
  "popularity": "cold",

  // Human-readable size string
  "size": "227.59MB",

  // Direct APK download URL (302 redirect from app store CDN)
  "download_url": "https://.../xxx.apk?md5=...",

  // MD5 checksum of the APK file (extracted from download_url or computed)
  "md5": "12848df71bbf7544800a7e6531078d7e",

  // Raw CDN query parameters as a nested object
  "cdn_params": {
    "md5": "12848df71bbf7544800a7e6531078d7e",
    "pkg": "com.psytap.wpyx",
    "vcode": "151",
    "fname": "5P医学",
    "nrd": "0",
    "productid": "2011",
    "packageid": "207182913",
    "appid": "7700065",
    "apprd": "7700065",
    "yingid": "wdj_web",
    "pos": "wdj_web/detail_normal_dl/0",
    "iconUrl": "http://.../con.png",
    "did": "2c0f14902176399e271124f37453bf6b"
  }
}
```

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `app_id` | string | yes | Source-specific unique identifier |
| `app_name` | string | yes | Human-readable display name (UTF-8) |
| `package_name` | string | yes | Android package name |
| `category` | string | yes | Primary category (Simplified Chinese) |
| `sub_category` | string | yes | Sub-category (Simplified Chinese) |
| `install_count` | string | no | Raw install count display text |
| `install_count_parsed` | integer | no | Parsed numeric install count |
| `popularity` | string | no | `"hot"`, `"normal"`, or `"cold"` |
| `size` | string | no | Human-readable APK file size |
| `download_url` | string | yes | Full URL to download the APK |
| `md5` | string | no | MD5 hex digest of the APK |
| `cdn_params` | object | no | Raw URL query parameters as key-value pairs |

## Usage Examples

### Python: Walk all apps

```python
import os, json

INFO_ROOT = "path/to/info"
APK_ROOT = "path/to/apk"

for category in os.listdir(INFO_ROOT):
    cat_path = os.path.join(INFO_ROOT, category)
    if not os.path.isdir(cat_path):
        continue
    for sub in os.listdir(cat_path):
        sub_path = os.path.join(cat_path, sub)
        for app_dir in os.listdir(sub_path):
            info_file = os.path.join(sub_path, app_dir, "info.json")
            if not os.path.exists(info_file):
                continue
            with open(info_file, encoding="utf-8") as f:
                app = json.load(f)
            yield app
```

### Python: Name-based APK lookup

```python
def find_apk(apk_root, package_name, version_name=None):
    """Find APK by package name (and optionally version)."""
    for root, dirs, files in os.walk(apk_root):
        for f in files:
            if not f.endswith(".apk"):
                continue
            parts = f.split("_")
            if package_name in f:
                if version_name is None or version_name in f:
                    return os.path.join(root, f)
    return None
```
