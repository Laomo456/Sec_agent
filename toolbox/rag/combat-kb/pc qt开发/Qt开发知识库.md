# PC Qt 开发知识库

## 目录
1. [环境配置](#环境配置)
2. [项目结构](#项目结构)
3. [CMakeLists.txt 配置](#cmakeliststxt-配置)
4. [编译与部署](#编译与部署)
5. [常见问题与解决方案](#常见问题与解决方案)
6. [Qt 样式表 (QSS)](#qt-样式表-qss)
7. [网络请求](#网络请求)
8. [信号槽机制](#信号槽机制)
9. [实战经验](#实战经验)

---

## 环境配置

### MSVC 编译器
```bat
# 调用 vcvars64.bat 设置环境变量
call "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
```

### Qt 路径
```
Qt 安装目录: D:\qt\6.11.2\msvc2022_64
CMake: C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe
Ninja: C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja\ninja.exe
```

### 环境变量
```bat
set PATH=D:\qt\6.11.2\msvc2022_64\bin;%PATH%
```

---

## 项目结构

```
project/
├── CMakeLists.txt          # CMake 配置文件
├── main.cpp                # 主入口
├── mainwindow.h            # 主窗口头文件
├── mainwindow.cpp          # 主窗口实现
├── src/                    # 源代码目录
│   ├── elf.cpp/h          # ELF 解析
│   ├── pe.cpp/h           # PE 解析
│   ├── jni.cpp/h          # JNI 提取
│   ├── packer.cpp/h       # 壳检测
│   └── strings.cpp/h      # 字符串提取
└── build/                  # 构建目录
```

---

## CMakeLists.txt 配置

### 基础配置
```cmake
cmake_minimum_required(VERSION 3.16)
project(revkit LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTORCC ON)
set(CMAKE_AUTOUIC ON)

# 查找 Qt 模块
find_package(Qt6 REQUIRED COMPONENTS 
    Core 
    Gui 
    Widgets 
    Network
)

# 源文件
set(SOURCES
    main.cpp
    mainwindow.cpp
    src/elf.cpp
    src/pe.cpp
    src/jni.cpp
    src/packer.cpp
    src/strings.cpp
)

# 头文件
set(HEADERS
    mainwindow.h
    src/elf.h
    src/pe.h
    src/jni.h
    src/packer.h
    src/strings.h
)

# 创建可执行文件
add_executable(${PROJECT_NAME} ${SOURCES} ${HEADERS})

# 链接 Qt 库
target_link_libraries(${PROJECT_NAME} PRIVATE
    Qt6::Core
    Qt6::Gui
    Qt6::Widgets
    Qt6::Network
)

# 包含目录
target_include_directories(${PROJECT_NAME} PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/src
)

# Windows 子系统（不显示控制台）
if(WIN32)
    set_target_properties(${PROJECT_NAME} PROPERTIES
        WIN32_EXECUTABLE TRUE
    )
endif()
```

### 常用 Qt 模块
```cmake
# 核心模块
Qt6::Core          # 核心非 GUI 功能
Qt6::Gui           # GUI 基础
Qt6::Widgets       # 窗口组件

# 网络模块
Qt6::Network       # 网络请求 (QNetworkAccessManager)

# 其他常用
Qt6::Sql           # 数据库
Qt6::Xml           # XML 处理
Qt6::Svg           # SVG 支持
Qt6::Concurrent    # 并发处理
```

---

## 编译与部署

### 编译脚本
```bat
@echo off
call "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1

set CMAKE="C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
set NINJA="C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja\ninja.exe"
set QT_PREFIX=D:\qt\6.11.2\msvc2022_64

cd /d %~dp0
rmdir /s /q build 2>nul

%CMAKE% -S . -B build -G Ninja ^
    -DCMAKE_PREFIX_PATH=%QT_PREFIX% ^
    -DCMAKE_BUILD_TYPE=Release ^
    -DCMAKE_MAKE_PROGRAM=%NINJA%

%CMAKE% --build build --config Release

if exist build\revkit.exe (
    echo [OK] revkit.exe built successfully
    dir build\revkit.exe
) else (
    echo [FAIL] build failed
)
```

### 部署 Qt 依赖
```bat
# 使用 windeployqt 自动复制所需的 Qt DLL
D:\qt\6.11.2\msvc2022_64\bin\windeployqt.exe build\revkit.exe

# 手动复制（如果 windeployqt 失败）
copy D:\qt\6.11.2\msvc2022_64\bin\Qt6Core.dll build\
copy D:\qt\6.11.2\msvc2022_64\bin\Qt6Gui.dll build\
copy D:\qt\6.11.2\msvc2022_64\bin\Qt6Widgets.dll build\
copy D:\qt\6.11.2\msvc2022_64\bin\Qt6Network.dll build\

# 复制平台插件
mkdir build\platforms 2>nul
copy D:\qt\6.11.2\msvc2022_64\plugins\platforms\qwindows.dll build\platforms\
```

### 必需的 DLL
```
Qt6Core.dll
Qt6Gui.dll
Qt6Widgets.dll
Qt6Network.dll      # 如果使用网络功能
Qt6Svg.dll          # 如果使用 SVG
D3Dcompiler_47.dll
opengl32sw.dll
platforms/qwindows.dll
```

---

## 常见问题与解决方案

### 1. 链接错误 LNK1104: 无法打开文件 "revkit.exe"
**原因**: 程序正在运行，文件被占用  
**解决**: 
```bat
taskkill /f /im revkit.exe
```

### 2. 运行时缺少 Qt6Network.dll
**原因**: 未部署 Qt 依赖  
**解决**:
```bat
D:\qt\6.11.2\msvc2022_64\bin\windeployqt.exe revkit.exe
```

### 3. 窗口不显示
**原因**: 
- 从后台进程启动，会话隔离
- 未调用 `show()`

**解决**:
```cpp
MainWindow w;
w.show();  // 必须调用
return app.exec();
```

### 4. 中文乱码
**原因**: 源文件编码问题  
**解决**:
```cmake
# CMakeLists.txt 添加
add_compile_options("$<$<CXX_COMPILER_ID:MSVC>:/utf-8>")
```

### 5. 样式不生效
**原因**: 
- 选择器错误
- 属性名拼写错误
- 优先级问题

**解决**:
```cpp
// 使用 setStyleSheet 而不是全局 QSS
widget->setStyleSheet("QPushButton { color: red; }");
```

### 6. 网络请求失败
**原因**: 
- SSL 证书问题
- 未初始化 QNetworkAccessManager
- URL 格式错误

**解决**:
```cpp
// 在构造函数中初始化
m_networkManager = new QNetworkAccessManager(this);

// 检查 URL
QUrl url("https://api.example.com/v1/chat");
if (!url.isValid()) {
    qDebug() << "Invalid URL";
}
```

---

## Qt 样式表 (QSS)

### 基础语法
```cpp
// 设置单个组件样式
QPushButton* btn = new QPushButton("按钮");
btn->setStyleSheet(
    "QPushButton {"
    "  background-color: #8b5cf6;"
    "  color: white;"
    "  border-radius: 8px;"
    "  padding: 10px 20px;"
    "}"
    "QPushButton:hover {"
    "  background-color: #a78bfa;"
    "}"
    "QPushButton:pressed {"
    "  background-color: #7c3aed;"
    "}"
);
```

### 常用样式属性
```css
/* 背景 */
background-color: #1e1e1e;
background-image: url(:/images/bg.png);

/* 边框 */
border: 1px solid #3e3e42;
border-radius: 6px;
border-top-left-radius: 8px;

/* 文字 */
color: #ffffff;
font-family: "Microsoft YaHei";
font-size: 14px;
font-weight: bold;

/* 内边距 */
padding: 10px;
padding-left: 20px;
padding: 10px 20px 10px 20px;

/* 外边距 */
margin: 5px;

/* 尺寸 */
min-width: 100px;
max-height: 50px;

/* 对齐 */
text-align: center;
qproperty-alignment: AlignCenter;
```

### 伪状态
```css
/* 悬停 */
QPushButton:hover { background: #555; }

/* 按下 */
QPushButton:pressed { background: #333; }

/* 禁用 */
QPushButton:disabled { color: #666; }

/* 选中 */
QCheckBox:checked { color: green; }

/* 聚焦 */
QLineEdit:focus { border-color: #0078d4; }
```

### 主题切换示例
```cpp
void MainWindow::applyTheme(int themeIndex) {
    switch (themeIndex) {
        case 0: // 深色主题
            m_logArea->setStyleSheet(
                "QTextEdit { background: #1e1e1e; color: #d4d4d4; "
                "border: 1px solid #3e3e42; }"
            );
            break;
        case 1: // 浅色主题
            m_logArea->setStyleSheet(
                "QTextEdit { background: #ffffff; color: #000000; "
                "border: 1px solid #cccccc; }"
            );
            break;
    }
}
```

---

## 网络请求

### 基础 GET 请求
```cpp
void MainWindow::makeGetRequest() {
    QNetworkRequest request;
    request.setUrl(QUrl("https://api.example.com/data"));
    request.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");
    
    QNetworkReply* reply = m_networkManager->get(request);
    
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        
        if (reply->error() != QNetworkReply::NoError) {
            qDebug() << "Error:" << reply->errorString();
            return;
        }
        
        QByteArray response = reply->readAll();
        QJsonDocument doc = QJsonDocument::fromJson(response);
        qDebug() << doc.object();
    });
}
```

### POST 请求（JSON）
```cpp
void MainWindow::makePostRequest(const QString& prompt) {
    QJsonObject requestBody;
    requestBody["model"] = "deepseek-v4-flash";
    requestBody["messages"] = QJsonArray{
        QJsonObject{
            {"role", "user"},
            {"content", prompt}
        }
    };
    
    QNetworkRequest request;
    request.setUrl(QUrl("https://api.example.com/v1/chat/completions"));
    request.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");
    request.setRawHeader("Authorization", "Bearer YOUR_API_KEY");
    
    QJsonDocument doc(requestBody);
    QNetworkReply* reply = m_networkManager->post(request, doc.toJson());
    
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        
        if (reply->error() != QNetworkReply::NoError) {
            qDebug() << "Error:" << reply->errorString();
            return;
        }
        
        QByteArray response = reply->readAll();
        QJsonDocument doc = QJsonDocument::fromJson(response);
        QString content = doc["choices"][0]["message"]["content"].toString();
        qDebug() << content;
    });
}
```

### 配置管理（QSettings）
```cpp
// 保存配置
QSettings settings("MyApp", "AI");
settings.setValue("api_key", "sk-xxx");
settings.setValue("base_url", "https://api.example.com");
settings.setValue("model", "deepseek-v4-flash");

// 读取配置
QSettings settings("MyApp", "AI");
QString apiKey = settings.value("api_key", "default_key").toString();
QString baseUrl = settings.value("base_url", "https://api.example.com").toString();
QString model = settings.value("model", "deepseek-v4-flash").toString();
```

---

## 信号槽机制

### 基础连接
```cpp
// Lambda 表达式
connect(button, &QPushButton::clicked, this, []() {
    qDebug() << "Button clicked";
});

// 成员函数
connect(button, &QPushButton::clicked, this, &MainWindow::onButtonClicked);

// 带参数
connect(slider, &QSlider::valueChanged, this, [](int value) {
    qDebug() << "Slider value:" << value;
});

// 自定义信号
class MyClass : public QObject {
    Q_OBJECT
signals:
    void dataReady(const QString& data);
public slots:
    void onDataReady(const QString& data) {
        qDebug() << "Received:" << data;
    }
};

// 连接自定义信号
connect(sender, &MyClass::dataReady, receiver, &MyClass::onDataReady);
```

### 常用信号
```cpp
// QPushButton
connect(btn, &QPushButton::clicked, this, &MainWindow::onClicked);
connect(btn, &QPushButton::pressed, this, &MainWindow:: onPressed);
connect(btn, &QPushButton::released, this, &MainWindow::onReleased);

// QLineEdit
connect(input, &QLineEdit::textChanged, this, &MainWindow::onTextChanged);
connect(input, &QLineEdit::returnPressed, this, &MainWindow::onReturnPressed);

// QComboBox
connect(combo, QOverload<int>::of(&QComboBox::currentIndexChanged), 
        this, &MainWindow::onIndexChanged);

// QTextEdit
connect(textEdit, &QTextEdit::textChanged, this, &MainWindow::onTextChanged);
```

---

## 实战经验

### 1. 项目初始化检查清单
- [ ] MSVC 环境变量已设置（vcvars64.bat）
- [ ] Qt 路径正确（CMAKE_PREFIX_PATH）
- [ ] CMakeLists.txt 包含所有必需模块
- [ ] 头文件和源文件都已添加到 CMakeLists.txt
- [ ] 构建目录已清理（rmdir /s /q build）

### 2. 部署检查清单
- [ ] 使用 windeployqt 复制 Qt DLL
- [ ] 测试在干净目录运行（无 Qt 环境变量）
- [ ] 检查 platforms/qwindows.dll 是否存在
- [ ] 如果使用网络，检查 SSL 库

### 3. 调试技巧
```cpp
// 输出调试信息
qDebug() << "Debug message";
qWarning() << "Warning message";
qCritical() << "Critical message";

// 输出到文件
QFile logFile("debug.log");
if (logFile.open(QIODevice::WriteOnly | QIODevice::Append)) {
    QTextStream out(&logFile);
    out << QDateTime::currentDateTime().toString() << ": " << message << "\n";
}

// 断点调试
// Visual Studio: F9 设置断点，F5 启动调试
```

### 4. 性能优化
```cpp
// 使用 QThread 处理耗时任务
QThread* thread = new QThread();
Worker* worker = new Worker();
worker->moveToThread(thread);

connect(thread, &QThread::started, worker, &Worker::process);
connect(worker, &Worker::finished, thread, &QThread::quit);
connect(worker, &Worker::finished, worker, &QObject::deleteLater);
connect(thread, &QThread::finished, thread, &QObject::deleteLater);

thread->start();

// 使用信号槽更新 UI（线程安全）
connect(worker, &Worker::resultReady, this, &MainWindow::onResult);
```

### 5. 常见陷阱
```cpp
// ❌ 错误：在子线程直接操作 UI
void Worker::process() {
    mainWindow->updateUI();  // 崩溃！
}

// ✅ 正确：使用信号槽
void Worker::process() {
    emit resultReady(result);  // 发送信号
}
// 在主线程连接信号到 UI 更新槽

// ❌ 错误：忘记删除 QNetworkReply
QNetworkReply* reply = manager->get(request);
// 内存泄漏

// ✅ 正确：及时删除
connect(reply, &QNetworkReply::finished, reply, &QNetworkReply::deleteLater);

// ❌ 错误：阻塞主线程
void MainWindow::onButtonClicked() {
    QThread::sleep(5);  // UI 冻结 5 秒
}

// ✅ 正确：使用异步
void MainWindow::onButtonClicked() {
    makeAsyncRequest();  // 不阻塞
}
```

---

## 参考资源

### 官方文档
- Qt 6 文档: https://doc.qt.io/qt-6/
- Qt 样式表参考: https://doc.qt.io/qt-6/stylesheet-reference.html
- QSS 选择器: https://doc.qt.io/qt-6/stylesheet-syntax.html

### 常用工具
- Qt Creator: IDE
- windeployqt: 部署工具
- qmake: 构建系统（CMake 更推荐）

### 社区
- Qt 论坛: https://forum.qt.io/
- Stack Overflow: [qt] 标签

---

**最后更新**: 2026-09-12  
**基于**: Qt 6.11.2 MSVC 2022 64-bit
