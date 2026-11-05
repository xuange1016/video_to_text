import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

ApplicationWindow {
    id: window
    title: "片语 · 视频文案提取"
    width: 1280
    height: 800
    minimumWidth: 860
    minimumHeight: 620
    visible: true
    color: "#f5f7f4"
    font.family: appState.fontFamily
    font.pixelSize: 14

    readonly property color ink: "#122820"
    readonly property color muted: "#566961"
    readonly property color green: "#166344"
    readonly property color mint: "#e2efe3"
    readonly property color border: "#dbe4db"
    readonly property var models: ["small", "medium"]
    readonly property var formats: ["both", "txt", "srt"]
    readonly property var modelLabels: ["快速 · small", "均衡 · medium"]
    readonly property var formatLabels: ["TXT + SRT", "TXT · 纯文案", "SRT · 带时间码"]
    property string page: "work"
    property string draft: ""
    property string batchFolder: appState.settings.root
    property string settingsFolder: appState.settings.root
    property bool timeline: false
    property bool chooseSettingFolder: false
    property int saveJob: 0
    property string message: ""
    readonly property int completed: appState.jobs.filter(function(job) { return job.status === "done" }).length
    readonly property int retryable: appState.jobs.filter(function(job) {
        return ["failed", "interrupted", "cancelled", "save_pending"].includes(job.status)
    }).length
    readonly property bool split: width >= 1150

    function duration(value) {
        if (typeof value !== "number" || value <= 0) return "待获取"
        const sec = Math.floor(value)
        return Math.floor(sec / 60) + ":" + ("0" + sec % 60).slice(-2)
    }

    function localPath(url) {
        const value = decodeURIComponent(url.toString())
        if (value.indexOf("file:///") === 0)
            return value.slice(8).replace(/\//g, "\\")
        return value.replace(/^file:\/\//, "//").replace(/\//g, "\\")
    }

    component Action: Button {
        id: control
        property bool primary: false
        property bool highlightedNav: false
        implicitHeight: 41
        implicitWidth: Math.max(86, label.implicitWidth + 30)
        leftPadding: 15
        rightPadding: 15
        hoverEnabled: true
        contentItem: Text {
            id: label
            text: control.text
            font.family: window.font.family
            font.pixelSize: 14
            font.bold: control.primary || control.highlightedNav
            color: !control.enabled ? "#677871" : control.primary ? "white" : window.ink
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 7
            color: !control.enabled ? "#e9eee9" : control.primary ?
                       (control.hovered ? "#104f36" : window.green) :
                       control.highlightedNav || control.hovered ? window.mint : "#ffffff"
            border.color: control.activeFocus ? window.green : control.primary ? "transparent" : window.border
        }
    }

    component Heading: Label {
        color: window.ink
        font.pixelSize: 19
        font.bold: true
    }

    component Hint: Label {
        color: window.muted
        wrapMode: Text.Wrap
    }

    component Surface: Rectangle {
        color: "#ffffff"
        border.color: window.border
        radius: 11
    }

    component Status: Rectangle {
        property string label: ""
        property string status: ""
        implicitWidth: statusLabel.implicitWidth + 20
        implicitHeight: 27
        radius: 13
        color: ["failed", "save_pending"].includes(status) ? "#fff0de" :
                  status === "done" ? "#e0f0df" : "#edf1ed"
        Label {
            id: statusLabel
            anchors.centerIn: parent
            text: parent.label
            color: parent.status === "failed" ? "#8b431c" :
                       parent.status === "done" ? window.green : window.muted
            font.pixelSize: 12
            font.bold: true
        }
    }

    component Meter: ProgressBar {
        id: meter
        implicitHeight: 8
        padding: 0
        background: Rectangle { color: "#e4ebe3"; radius: 4 }
        contentItem: Item {
            Rectangle {
                height: parent.height
                width: meter.visualPosition * parent.width
                radius: 4
                color: window.green
            }
        }
    }

    FolderDialog {
        id: sourceDialog
        title: "选择要扫描的文件夹"
        onAccepted: appState.addFolder(selectedFolder)
    }
    FolderDialog {
        id: outputDialog
        title: "选择文稿保存目录"
        onAccepted: {
            const folder = window.localPath(selectedFolder)
            if (window.chooseSettingFolder) {
                window.settingsFolder = folder
                defaultOutput.text = folder
            } else {
                window.batchFolder = folder
                batchOutput.text = folder
            }
        }
    }
    FolderDialog {
        id: recoverDialog
        title: "换一个目录保存文稿"
        onAccepted: appState.retry(window.saveJob, window.localPath(selectedFolder))
    }
    Dialog {
        id: quitDialog
        title: "结束当前处理？"
        anchors.centerIn: parent
        modal: true
        standardButtons: Dialog.Ok | Dialog.Cancel
        Label { text: "已完成的结果会保留，剩余项可在任务记录中继续。" }
        onAccepted: appState.cancelAndQuit()
    }
    onClosing: function(event) {
        if (appState.busy) {
            event.accepted = false
            quitDialog.open()
        }
    }
    Connections {
        target: appState
        function onFeedback(value) {
            window.message = value
            toastTimer.restart()
        }
    }
    Timer { id: toastTimer; interval: 8500; onTriggered: window.message = "" }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            Layout.fillHeight: true
            Layout.preferredWidth: 172
            color: "#edf2ed"
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 14
                spacing: 10
                RowLayout {
                    Layout.fillWidth: true
                    Layout.topMargin: 10
                    Layout.bottomMargin: 25
                    spacing: 9
                    Rectangle {
                        Layout.preferredWidth: 43
                        Layout.preferredHeight: 43
                        radius: 10
                        color: "#123d2c"
                        Row {
                            anchors.centerIn: parent
                            spacing: 3
                            Repeater {
                                model: [10, 17, 25, 16, 9]
                                Rectangle { required property int modelData; width: 2; height: modelData
                                    y: (25 - height) / 2; radius: 1; color: "#e0f7e8" }
                            }
                        }
                    }
                    ColumnLayout {
                        spacing: 0
                        Heading { text: "片语"; font.pixelSize: 25 }
                        Hint { text: "视频转文字"; font.pixelSize: 11 }
                    }
                }
                Action {
                    Layout.fillWidth: true
                    text: "文案提取"
                    highlightedNav: window.page === "work"
                    onClicked: window.page = "work"
                }
                Action {
                    Layout.fillWidth: true
                    text: "任务记录"
                    highlightedNav: window.page === "history"
                    onClicked: window.page = "history"
                }
                Action {
                    Layout.fillWidth: true
                    text: "设置"
                    highlightedNav: window.page === "settings"
                    onClicked: window.page = "settings"
                }
                Item { Layout.fillHeight: true }
                Hint { text: "在本机识别 · Windows"; font.pixelSize: 11
                    Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.margins: 24
            spacing: 14
            Label {
                text: "工作台  /  " + ({work: "文案提取", history: "任务记录", settings: "设置"})[window.page]
                color: window.muted
                font.pixelSize: 13
            }
            Surface {
                Layout.fillWidth: true
                Layout.preferredHeight: 91
                color: window.mint
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 17
                    spacing: 2
                    Heading {
                        text: ({work: "视频文案提取", history: "任务记录", settings: "应用设置"})[window.page]
                        font.pixelSize: 29
                    }
                    Hint {
                        text: ({work: "粘贴分享链接或选择本地文件夹，批量提取视频里的口播文案。",
                                history: "查看本机保存的批次、文稿与待处理任务。",
                                settings: "设置默认保存位置、导出格式和中文识别模型。"})[window.page]
                    }
                }
            }

            RowLayout {
                visible: window.page === "work"
                spacing: 8
                Repeater {
                    model: ["① 导入素材", "② 确认素材", "③ 提取与结果"]
                    Status {
                        required property int index
                        required property string modelData
                        label: modelData
                        status: appState.stage === index + 1 ? "done" : ""
                    }
                }
                Item { Layout.fillWidth: true }
                Action {
                    visible: appState.stage === 3
                    enabled: !appState.busy
                    text: "新建批次"
                    onClicked: {
                        window.draft = ""
                        input.text = ""
                        window.batchFolder = appState.settings.root
                        batchOutput.text = appState.settings.root
                        batchModel.currentIndex = window.models.indexOf(appState.settings.model)
                        batchFormat.currentIndex = window.formats.indexOf(appState.settings.format)
                        appState.newBatch()
                    }
                }
            }

            StackLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                currentIndex: ({work: 0, history: 1, settings: 2})[window.page]

                StackLayout {
                    currentIndex: appState.stage - 1

                    Surface {
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 19
                            spacing: 12
                            Heading { text: "导入视频或音频" }
                            Hint { text: "支持抖音、B 站分享链接和本地绝对路径。可以一次粘贴多条，建议每条占一行。" }
                            ScrollView {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                TextArea {
                                    id: input
                                    objectName: "sourceInput"
                                    text: window.draft
                                    onTextChanged: window.draft = text
                                    placeholderText: "粘贴分享文本、链接或本地文件路径\n例如 https://v.douyin.com/…\n或 D:\\视频素材\\采访.mp4"
                                    wrapMode: TextEdit.Wrap
                                    selectByMouse: true
                                    padding: 13
                                    background: Rectangle { color: "#f6f9f5"; radius: 7; border.color: window.border }
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Action { text: "选择文件夹"; onClicked: sourceDialog.open() }
                                Hint { text: "会扫描所选文件夹及子文件夹"; Layout.fillWidth: true }
                                Action {
                                    objectName: "resolveMaterials"
                                    text: appState.task === "resolve" ? "正在解析…" : "解析素材  →"
                                    primary: true
                                    enabled: !appState.busy
                                    onClicked: appState.resolve(input.text)
                                }
                            }
                            Flow {
                                Layout.fillWidth: true
                                spacing: 6
                                Repeater {
                                    model: appState.folders
                                    Action {
                                        required property string modelData
                                        text: modelData + "  ×"
                                        enabled: !appState.busy
                                        onClicked: appState.removeFolder(modelData)
                                    }
                                }
                            }
                        }
                    }

                    Surface {
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 16
                            spacing: 9
                            RowLayout {
                                Layout.fillWidth: true
                                Heading { text: "确认素材" }
                                Item { Layout.fillWidth: true }
                                Action {
                                    objectName: "selectAllMaterials"
                                    text: "全选可用 / 清除全选"
                                    enabled: !appState.busy
                                    onClicked: appState.selectAll()
                                }
                            }
                            Hint {
                                Layout.fillWidth: true
                                text: "抖音 " + (appState.counts["抖音"] || 0) + " 条  ·  B 站 " +
                                      (appState.counts["B 站"] || 0) + " 条  ·  本地 " +
                                      (appState.counts["本地文件"] || 0) + " 个  ·  重复 " +
                                      appState.counts.duplicates + "  ·  无效 " + appState.counts.invalid
                            }
                            Rectangle {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 32
                                color: "#eaf2e9"
                                radius: 5
                                Label { anchors.left: parent.left; anchors.leftMargin: 13; anchors.verticalCenter: parent.verticalCenter
                                    text: "素材 / 状态"; color: window.ink; font.bold: true }
                            }
                            ListView {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                clip: true
                                model: appState.inputs
                                ScrollBar.vertical: ScrollBar {}
                                delegate: Rectangle {
                                    id: materialRow
                                    required property var modelData
                                    width: ListView.view.width - 6
                                    height: modelData.error ? 61 : 48
                                    color: modelData.checked ? "#f0f7ef" : "white"
                                    Rectangle { height: 1; width: parent.width; anchors.bottom: parent.bottom; color: window.border }
                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.leftMargin: 4
                                        anchors.rightMargin: 9
                                        CheckBox {
                                            Accessible.name: materialRow.modelData.title
                                            enabled: !materialRow.modelData.error && !appState.busy
                                            checked: materialRow.modelData.checked
                                            onClicked: appState.selectItem(materialRow.modelData.key, checked)
                                        }
                                        ColumnLayout {
                                            Layout.fillWidth: true
                                            spacing: 1
                                            Label { Layout.fillWidth: true; elide: Text.ElideRight; textFormat: Text.PlainText
                                                text: materialRow.modelData.title; color: window.ink }
                                            Hint { Layout.fillWidth: true; text: materialRow.modelData.error; visible: text.length > 0; font.pixelSize: 11 }
                                        }
                                        Status { label: materialRow.modelData.source }
                                        Hint { text: materialRow.modelData.error ? "无效" : window.duration(materialRow.modelData.duration) }
                                    }
                                }
                            }
                            GridLayout {
                                Layout.fillWidth: true
                                columns: 4
                                columnSpacing: 8
                                rowSpacing: 3
                                Label { text: "保存目录"; color: window.ink }
                                TextField { id: batchOutput; objectName: "batchOutput"; Layout.fillWidth: true; text: window.batchFolder
                                    selectByMouse: true; onTextEdited: window.batchFolder = text }
                                Action { text: "浏览"; onClicked: { window.chooseSettingFolder = false; outputDialog.open() } }
                                Hint { text: "中文 · 简体输出"; font.pixelSize: 12 }
                                Label { text: "转写模型"; color: window.ink }
                                RowLayout {
                                    ComboBox { id: batchModel; model: window.modelLabels; Layout.preferredWidth: 166
                                        currentIndex: window.models.indexOf(appState.settings.model) }
                                    Hint {
                                        font.pixelSize: 12
                                        text: appState.modelsInfo[window.models[batchModel.currentIndex]]?.ready ?
                                                  "已缓存" : "首次转写时下载 " +
                                                  (appState.modelsInfo[window.models[batchModel.currentIndex]]?.size || "")
                                    }
                                }
                                Label { text: "导出格式"; color: window.ink }
                                ComboBox { id: batchFormat; model: window.formatLabels; Layout.preferredWidth: 161
                                    currentIndex: window.formats.indexOf(appState.settings.format) }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Action { text: "← 返回导入"; enabled: !appState.busy; onClicked: appState.back() }
                                Item { Layout.fillWidth: true }
                                Action {
                                    objectName: "submitBatch"
                                    primary: true
                                    enabled: appState.selectedCount > 0 && !appState.busy
                                    text: appState.task === "batch" ? "正在准备…" : "提取 " + appState.selectedCount + " 条文案"
                                    onClicked: appState.submit(batchOutput.text, window.models[batchModel.currentIndex],
                                                               window.formats[batchFormat.currentIndex])
                                }
                            }
                        }
                    }

                    ColumnLayout {
                        spacing: 9
                        RowLayout {
                            Layout.fillWidth: true
                            Heading { text: "已完成 " + window.completed + " / " + appState.jobs.length }
                            Meter { Layout.fillWidth: true; from: 0; to: Math.max(1, appState.jobs.length); value: window.completed }
                            Action {
                                visible: appState.canCancel
                                text: "取消本批"
                                onClicked: appState.cancel()
                            }
                            Action {
                                visible: window.retryable > 0 && !appState.busy
                                text: "重试未完成项"
                                onClicked: appState.retry(0, "")
                            }
                        }
                        GridLayout {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            columns: window.split ? 2 : 1
                            columnSpacing: 12
                            rowSpacing: 10
                            Surface {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                Layout.preferredWidth: window.split ? 540 : 800
                                Layout.minimumHeight: window.split ? 0 : 115
                                ColumnLayout {
                                    anchors.fill: parent
                                    anchors.margins: 12
                                    Heading { text: "提取任务" }
                                    ListView {
                                        Layout.fillWidth: true
                                        Layout.fillHeight: true
                                        clip: true
                                        model: appState.jobs
                                        ScrollBar.vertical: ScrollBar {}
                                        delegate: ItemDelegate {
                                            id: jobRow
                                            required property var modelData
                                            width: ListView.view.width - 5
                                            height: modelData.error ? 68 : 58
                                            onClicked: appState.openJob(modelData.id)
                                            background: Rectangle {
                                                color: appState.detail.id === jobRow.modelData.id ? window.mint :
                                                           jobRow.hovered ? "#f4f7f3" : "#ffffff"
                                                radius: 6
                                            }
                                            contentItem: RowLayout {
                                                spacing: 6
                                                ColumnLayout {
                                                    Layout.fillWidth: true
                                                    spacing: 1
                                                    Label { Layout.fillWidth: true; elide: Text.ElideRight; color: window.ink; textFormat: Text.PlainText
                                                        text: jobRow.modelData.title }
                                                    Hint { Layout.fillWidth: true; font.pixelSize: 11
                                                        text: jobRow.modelData.error || jobRow.modelData.source }
                                                    Meter { Layout.fillWidth: true
                                                        visible: typeof jobRow.modelData.progress === "number" && jobRow.modelData.status !== "done"
                                                        value: jobRow.modelData.progress || 0 }
                                                }
                                                Status { label: jobRow.modelData.stateLabel; status: jobRow.modelData.status }
                                            }
                                        }
                                    }
                                }
                            }
                            Surface {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                Layout.preferredWidth: window.split ? 360 : 800
                                Layout.minimumHeight: window.split ? 0 : 172
                                ColumnLayout {
                                    anchors.fill: parent
                                    anchors.margins: 12
                                    spacing: 6
                                    Heading { text: "文案预览" }
                                    Label {
                                        Layout.fillWidth: true
                                        text: appState.detail.title || "选择一条任务"
                                        color: window.ink
                                        textFormat: Text.PlainText
                                        elide: Text.ElideRight
                                        font.bold: true
                                    }
                                    Hint {
                                        Layout.fillWidth: true
                                        text: appState.detail.transcriptSource || appState.detail.error ||
                                              appState.detail.stateLabel || "任务完成后，文案会显示在这里。"
                                    }
                                    RowLayout {
                                        visible: Boolean(appState.detail.hasResult)
                                        Action { text: "纯文案"; highlightedNav: !window.timeline; onClicked: window.timeline = false }
                                        Action { text: "时间轴"; highlightedNav: window.timeline; onClicked: window.timeline = true }
                                    }
                                    ScrollView {
                                        Layout.fillWidth: true
                                        Layout.fillHeight: true
                                        TextArea {
                                            readOnly: true
                                            selectByMouse: true
                                            wrapMode: TextEdit.Wrap
                                            padding: 8
                                            text: window.timeline && appState.detail.timeline ?
                                                      appState.detail.timeline.map(function(row) { return row.stamp + "   " + row.text }).join("\n") :
                                                      appState.detail.text || ""
                                            background: Rectangle { radius: 6; color: "#f2f7f0" }
                                        }
                                    }
                                    Flow {
                                        Layout.fillWidth: true
                                        spacing: 6
                                        Action {
                                            text: "复制文案"; visible: Boolean(appState.detail.hasResult)
                                            onClicked: appState.copy(appState.detail.text)
                                        }
                                        Action {
                                            text: "打开保存目录"; visible: appState.detail.status === "done"
                                            onClicked: appState.openFolder(appState.detail.destination)
                                        }
                                        Action {
                                            text: "用语音重转"; visible: appState.detail.transcriptSource === "平台字幕"
                                            enabled: !appState.busy
                                            onClicked: appState.forceAudio(appState.detail.id)
                                        }
                                        Action {
                                            text: "更换目录补存"; visible: appState.detail.status === "save_pending"
                                            enabled: !appState.busy
                                            onClicked: { window.saveJob = appState.detail.id; recoverDialog.open() }
                                        }
                                        Action {
                                            text: "重试这一条"
                                            visible: ["failed", "cancelled", "interrupted"].includes(appState.detail.status)
                                            enabled: !appState.busy
                                            onClicked: appState.retry(appState.detail.id, "")
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                Surface {
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 17
                        Heading { text: "历史批次" }
                        Hint { visible: !appState.history.length; text: "提交第一次提取后，批次会显示在这里。" }
                        ListView {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true
                            model: appState.history
                            ScrollBar.vertical: ScrollBar {}
                            delegate: ItemDelegate {
                                id: record
                                required property var modelData
                                width: ListView.view.width - 6
                                height: 54
                                text: "批次 " + modelData.id + "    " + modelData.created.slice(0, 16).replace("T", " ") +
                                      "    完成 " + modelData.done + " / " + modelData.total + "    待处理或失败 " + modelData.failed
                                onClicked: { appState.openBatch(record.modelData.id); window.page = "work" }
                            }
                        }
                    }
                }

                Surface {
                    ScrollView {
                        anchors.fill: parent
                        anchors.margins: 18
                        clip: true
                        contentWidth: availableWidth
                        ColumnLayout {
                            width: parent.width
                            spacing: 13
                            Heading { text: "文件保存" }
                            Hint { text: "每个视频按标题建立文件夹。已生成的文稿可从任务记录补存。"; Layout.fillWidth: true }
                            RowLayout {
                                Layout.fillWidth: true
                                TextField {
                                    id: defaultOutput
                                    Layout.fillWidth: true
                                    text: window.settingsFolder
                                    selectByMouse: true
                                    onTextEdited: window.settingsFolder = text
                                }
                                Action { text: "选择目录"; onClicked: { window.chooseSettingFolder = true; outputDialog.open() } }
                            }
                            RowLayout {
                                Label { text: "默认导出格式" }
                                ComboBox { id: defaultFormat; model: window.formatLabels; Layout.preferredWidth: 161
                                    currentIndex: window.formats.indexOf(appState.settings.format) }
                            }
                            Heading { text: "转写模型"; Layout.topMargin: 12 }
                            Repeater {
                                model: window.models
                                Surface {
                                    id: modelCard
                                    required property string modelData
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 82
                                    color: "#f7faf6"
                                    ColumnLayout {
                                        anchors.fill: parent
                                        anchors.margins: 10
                                        RowLayout {
                                            Heading { font.pixelSize: 15; text: modelCard.modelData === "small" ? "快速 · small" : "均衡 · medium" }
                                            Status {
                                                label: appState.modelsInfo[modelCard.modelData].ready ? "已缓存" : "需下载"
                                                status: appState.modelsInfo[modelCard.modelData].ready ? "done" : ""
                                            }
                                        }
                                        Hint {
                                            Layout.fillWidth: true
                                            text: (modelCard.modelData === "small" ? "清晰口播与大批素材初稿，资源占用较低。" :
                                                   "适合常规中文口播，专业词和有背景声时优先试用。") +
                                                  "  " + appState.modelsInfo[modelCard.modelData].size
                                        }
                                    }
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Label { text: "默认模型" }
                                ComboBox { id: defaultModel; model: window.modelLabels; Layout.preferredWidth: 166
                                    currentIndex: window.models.indexOf(appState.settings.model) }
                                Item { Layout.fillWidth: true }
                                Action {
                                    text: "保存设置"; primary: true
                                    onClicked: {
                                        appState.saveSettings(defaultOutput.text, window.models[defaultModel.currentIndex],
                                                              window.formats[defaultFormat.currentIndex])
                                        if (appState.stage === 1) {
                                            batchOutput.text = appState.settings.root
                                            batchModel.currentIndex = window.models.indexOf(appState.settings.model)
                                            batchFormat.currentIndex = window.formats.indexOf(appState.settings.format)
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            Surface {
                Layout.fillWidth: true
                Layout.preferredHeight: toast.implicitHeight + 13
                visible: window.message !== ""
                color: "#f2f7ef"
                Hint { id: toast; anchors.left: parent.left; anchors.right: parent.right
                    anchors.margins: 7; anchors.verticalCenter: parent.verticalCenter; text: window.message }
            }
        }
    }
}
