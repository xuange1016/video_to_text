import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
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
    color: "#f9f6ef"
    font.family: appState.fontFamily
    font.pixelSize: 14
    palette.window: ivory
    palette.base: ivory
    palette.button: inset
    palette.buttonText: ink
    palette.windowText: ink
    palette.text: ink
    palette.highlight: accent
    palette.highlightedText: ivory

    readonly property color ink: "#292d26"
    readonly property color muted: "#656559"
    readonly property color accent: "#a04434"
    readonly property color blush: "#f1e0d7"
    readonly property color border: "#e3dccf"
    readonly property color inset: "#efeae0"
    readonly property color ivory: "#fffcf7"
    readonly property color rail: "#26382e"
    readonly property color railText: "#f5eee0"
    readonly property color leaf: "#315d44"
    readonly property color leafWash: "#e6eddb"
    readonly property bool motion: !appState.settings.reducedMotion
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
    property bool chooseEditingFolder: false
    property int saveJob: 0
    property string message: ""
    property string resultBack: "queue"
    property string editError: ""
    property var editing: ({})
    property var removing: ({})
    property var queueIndex: ({})
    readonly property bool parsing: appState.task === "resolve"
    readonly property bool smallBusy: ["downloading", "stopping"].includes(appState.smallDownload.status)
    readonly property var queueJobs: appState.activeJobs.concat(appState.waitingJobs)
    readonly property int completed: appState.jobs.filter(function(job) { return job.status === "done" }).length
    readonly property int retryable: appState.jobs.filter(function(job) {
        return ["failed", "interrupted", "cancelled", "save_pending"].includes(job.status)
    }).length
    readonly property bool split: width >= 1150
    onQueueJobsChanged: if (queueModel) syncQueue()
    Component.onCompleted: syncQueue()

    ListModel { id: queueModel; objectName: "queueModel" }

    function syncQueue() {
        const next = {}
        const order = []
        for (const job of queueJobs) {
            if (next[job.id]) continue
            next[job.id] = job
            order.push(job.id)
        }
        queueIndex = next
        const kept = {}
        for (let i = queueModel.count - 1; i >= 0; --i) {
            const id = queueModel.get(i).jobId
            if (!next[id] || kept[id]) queueModel.remove(i)
            else kept[id] = true
        }
        for (let i = 0; i < order.length; ++i) {
            const id = order[i]
            if (i < queueModel.count && queueModel.get(i).jobId === id) continue
            let oldPosition = i + 1
            while (oldPosition < queueModel.count && queueModel.get(oldPosition).jobId !== id)
                ++oldPosition
            if (oldPosition < queueModel.count) queueModel.move(oldPosition, i, 1)
            else queueModel.insert(i, {jobId: id})
        }
    }

    function jobTitle(job) {
        return job.displayTitle || job.title || ""
    }

    function measured(job) {
        return typeof job.progress === "number" && job.status !== "done"
    }

    function progressLabel(job) {
        if (!measured(job)) return ""
        const pct = Math.floor(job.progress * 100) + "%"
        if (typeof job.eta !== "number" || job.eta < 0) return pct
        const seconds = Math.max(1, Math.ceil(job.eta))
        return pct + " · 剩余约 " + (seconds < 60 ? seconds + " 秒" : duration(seconds))
    }

    function openResult(batch, job, backPage) {
        resultBack = backPage
        timeline = false
        message = ""
        appState.viewBatch(batch)
        if (job) appState.openJob(job)
        window.page = "result"
    }

    function navigate(target) {
        if (target === "work" && appState.stage === 3) appState.newBatch()
        window.message = ""
        window.page = target
    }

    function duration(value) {
        if (typeof value !== "number" || value <= 0) return "时长待获取"
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
        property bool onRail: false
        property bool quiet: false
        property string navGlyph: ""
        implicitHeight: 41
        implicitWidth: Math.max(86, label.implicitWidth + 30)
        leftPadding: onRail ? 43 : 15
        rightPadding: 15
        hoverEnabled: true
        onPressed: if (window.motion) touchWave.play(pressX - 4, pressY - 4)
        contentItem: Text {
            id: label
            text: control.text
            font.family: window.font.family
            font.pixelSize: 14
            font.weight: control.primary || control.highlightedNav ? Font.DemiBold : Font.Normal
            color: control.onRail ? window.railText : !control.enabled ? window.muted : control.primary ? "white" : window.ink
            horizontalAlignment: control.onRail ? Text.AlignLeft : Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 8
            color: control.onRail ? (control.highlightedNav ? "#435047" : control.hovered ? "#343f36" : "transparent")
                   : !control.enabled ? "#e8e1d5" : control.primary ? (control.down ? "#7c3126" : control.hovered ? "#8c372a" : window.accent)
                   : control.highlightedNav ? window.blush : control.down ? "#d9e3ce" : control.hovered ? window.leafWash
                   : control.quiet ? "transparent" : window.inset
            border.width: control.visualFocus ? 2 : 0
            border.color: control.onRail ? "#efb99d" : window.leaf
            Behavior on color { enabled: window.motion && !control.primary; ColorAnimation { duration: 125; easing.type: Easing.OutCubic } }
            Ripple {
                id: touchWave
                anchors.fill: parent
                anchors.margins: 4
                tint: control.primary || control.onRail ? "#fff3d6" : window.leaf
            }
        }
        Canvas {
            visible: control.navGlyph !== ""
            width: 17
            height: 17
            anchors.left: parent.left
            anchors.leftMargin: 15
            anchors.verticalCenter: parent.verticalCenter
            onPaint: {
                const ctx = getContext("2d")
                ctx.reset()
                ctx.scale(width / 24, height / 24)
                ctx.strokeStyle = window.railText
                ctx.lineWidth = 1.7
                ctx.lineCap = "round"
                ctx.lineJoin = "round"
                ctx.beginPath()
                if (control.navGlyph === "import") {
                    ctx.moveTo(4, 14); ctx.lineTo(4, 19); ctx.lineTo(20, 19); ctx.lineTo(20, 14)
                    ctx.moveTo(12, 4); ctx.lineTo(12, 14)
                    ctx.moveTo(8, 10); ctx.lineTo(12, 14); ctx.lineTo(16, 10)
                } else if (control.navGlyph === "queue") {
                    for (let y = 6; y <= 18; y += 6) {
                        ctx.moveTo(4, y); ctx.lineTo(6, y)
                        ctx.moveTo(10, y); ctx.lineTo(y === 18 ? 16 : 20, y)
                    }
                } else if (control.navGlyph === "history") {
                    ctx.arc(12, 12, 8, -2.85, 3.05)
                    ctx.moveTo(3, 7); ctx.lineTo(4.1, 10.2); ctx.lineTo(7.3, 9.3)
                    ctx.moveTo(12, 8); ctx.lineTo(12, 12); ctx.lineTo(15, 14)
                } else if (control.navGlyph === "settings") {
                    ctx.moveTo(4, 7); ctx.lineTo(20, 7)
                    ctx.moveTo(4, 17); ctx.lineTo(20, 17)
                    ctx.moveTo(9, 4); ctx.lineTo(9, 10)
                    ctx.moveTo(15, 14); ctx.lineTo(15, 20)
                }
                ctx.stroke()
            }
        }
    }

    component Ripple: Item {
        id: pulse
        property color tint: window.leaf
        clip: true
        function play(x, y) {
            ring.x = Math.max(0, Math.min(width, x)) - ring.width / 2
            ring.y = Math.max(0, Math.min(height, y)) - ring.height / 2
            spread.restart()
        }
        Rectangle {
            id: ring
            width: Math.max(1, pulse.width, pulse.height)
            height: width
            radius: width / 2
            color: "transparent"
            border.width: 2
            border.color: pulse.tint
            opacity: 0
        }
        ParallelAnimation {
            id: spread
            NumberAnimation { target: ring; property: "scale"; from: 0.06; to: 1.75; duration: 340; easing.type: Easing.OutCubic }
            NumberAnimation { target: ring; property: "opacity"; from: 0.38; to: 0; duration: 340; easing.type: Easing.OutQuad }
        }
    }

    component Choice: Basic.ComboBox {
        id: choice
        implicitHeight: 40
        implicitWidth: 162
        leftPadding: 12
        rightPadding: 34
        hoverEnabled: true
        contentItem: Text {
            text: choice.displayText
            textFormat: Text.PlainText
            color: choice.enabled ? window.ink : window.muted
            font: choice.font
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        indicator: Canvas {
            x: choice.width - 23
            y: (choice.height - height) / 2
            width: 13
            height: 13
            rotation: choice.popup.visible ? 180 : 0
            Behavior on rotation { enabled: window.motion; NumberAnimation { duration: 145; easing.type: Easing.OutCubic } }
            onPaint: {
                const c = getContext("2d")
                c.reset()
                c.strokeStyle = window.leaf
                c.lineWidth = 1.6
                c.lineJoin = "round"
                c.lineCap = "round"
                c.beginPath()
                c.moveTo(2.5, 5); c.lineTo(6.5, 9); c.lineTo(10.5, 5)
                c.stroke()
            }
        }
        background: Rectangle {
            radius: 9
            color: !choice.enabled ? window.inset : choice.hovered || choice.popup.visible ? window.leafWash : window.color
            border.width: choice.visualFocus ? 2 : 1
            border.color: choice.visualFocus || choice.popup.visible ? window.leaf : "#d9d4c6"
            Behavior on color { enabled: window.motion; ColorAnimation { duration: 135 } }
        }
        delegate: Basic.ItemDelegate {
            id: option
            required property int index
            required property string modelData
            objectName: choice.objectName + "Option" + index
            width: ListView.view ? ListView.view.width : choice.width
            height: 38
            leftPadding: 12
            rightPadding: 24
            hoverEnabled: true
            highlighted: choice.highlightedIndex === index
            Accessible.name: modelData
            contentItem: Text {
                text: option.modelData
                textFormat: Text.PlainText
                color: window.ink
                font: choice.font
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
            }
            background: Rectangle {
                radius: 7
                color: option.hovered || option.highlighted ? "#dce8d1" :
                       option.index === choice.currentIndex ? "#edf2e5" : "transparent"
                Behavior on color { enabled: window.motion; ColorAnimation { duration: 110 } }
                Rectangle {
                    visible: option.index === choice.currentIndex
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.right: parent.right
                    anchors.rightMargin: 12
                    width: 7
                    height: 7
                    radius: 4
                    color: window.leaf
                }
            }
        }
        popup: Popup {
            y: choice.height + 5
            width: Math.max(choice.width, 165)
            height: Math.min(choice.count * 38, 228) + topPadding + bottomPadding
            padding: 5
            margins: 8
            contentItem: ListView {
                clip: true
                model: choice.popup.visible ? choice.delegateModel : null
                currentIndex: choice.highlightedIndex
                ScrollBar.vertical: ScrollBar {}
            }
            background: Rectangle { radius: 12; color: window.ivory; border.color: "#cad6bf" }
            enter: Transition {
                NumberAnimation { property: "scale"; from: 0.98; to: 1; duration: window.motion ? 130 : 0; easing.type: Easing.OutCubic }
            }
        }
    }

    component Field: TextField {
        id: field
        color: window.ink
        selectionColor: window.accent
        selectedTextColor: window.ivory
        placeholderTextColor: window.muted
        selectByMouse: true
        hoverEnabled: true
        padding: 10
        background: Rectangle {
            radius: 7
            color: field.activeFocus ? window.ivory : field.hovered ? "#e9eddf" : window.inset
            border.width: field.activeFocus ? 2 : 0
            border.color: window.leaf
            Behavior on color { enabled: window.motion; ColorAnimation { duration: 135 } }
        }
    }

    component PathField: Field {
        id: pathField
        font.underline: pathLink.hovered
        Accessible.description: "按住 Ctrl 单击，或按 Ctrl 和回车，在资源管理器打开目录。"
        HoverHandler { id: pathLink; acceptedModifiers: Qt.ControlModifier; cursorShape: Qt.PointingHandCursor }
        TapHandler {
            acceptedButtons: Qt.LeftButton
            acceptedModifiers: Qt.ControlModifier
            gesturePolicy: TapHandler.DragThreshold
            onTapped: appState.openFolder(pathField.text)
        }
        Keys.onPressed: function(event) {
            if ((event.modifiers & Qt.ControlModifier) && (event.key === Qt.Key_Return || event.key === Qt.Key_Enter)) {
                appState.openFolder(pathField.text)
                event.accepted = true
            }
        }
    }

    component Heading: Label {
        color: window.ink
        font.pixelSize: 19
        font.weight: Font.DemiBold
        Accessible.name: text
    }

    component Hint: Label {
        color: window.muted
        wrapMode: Text.Wrap
        Accessible.name: text
    }

    component Surface: Rectangle {
        color: window.ivory
        radius: 15
    }

    component Status: Rectangle {
        property string label: ""
        property string status: ""
        implicitWidth: statusLabel.implicitWidth + 20
        implicitHeight: 27
        radius: 13
        color: ["failed", "save_pending"].includes(status) ? window.blush :
                  status === "done" ? "#e6ebe0" : window.inset
        Label {
            id: statusLabel
            anchors.centerIn: parent
            text: parent.label
            Accessible.name: text
            color: parent.status === "failed" ? window.accent :
                       parent.status === "done" ? "#3f5d46" : window.muted
            font.pixelSize: 12
            font.bold: true
        }
    }

    component Meter: ProgressBar {
        id: meter
        property bool flowing: false
        implicitHeight: 5
        padding: 0
        background: Rectangle { color: "#e9dfd2"; radius: 3 }
        contentItem: Item {
            Rectangle {
                id: meterFill
                height: parent.height
                width: meter.indeterminate ? 0 : meter.visualPosition * parent.width
                radius: 3
                color: window.leaf
                clip: true
                Behavior on width { enabled: window.motion; NumberAnimation { duration: 165; easing.type: Easing.OutCubic } }
            }
            Item {
                anchors.fill: meter.indeterminate ? parent : meterFill
                clip: true
                visible: (meter.indeterminate || (window.motion && meter.flowing)) && meter.visible
                Rectangle {
                    id: current
                    width: Math.min(parent.width * 0.28, 68)
                    height: parent.height
                    radius: 3
                    x: meter.indeterminate ? (parent.width - width) / 2 : -width
                    gradient: Gradient {
                        orientation: Gradient.Horizontal
                        GradientStop { position: 0; color: "transparent" }
                        GradientStop { position: 0.5; color: meter.indeterminate ? window.leaf : "#b5cba0" }
                        GradientStop { position: 1; color: "transparent" }
                    }
                    NumberAnimation on x {
                        running: meter.visible && window.motion && (meter.flowing || meter.indeterminate)
                        from: -current.width
                        to: current.parent.width
                        duration: 1220
                        loops: Animation.Infinite
                    }
                }
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
            if (window.chooseEditingFolder) {
                editOutput.text = folder
            } else if (window.chooseSettingFolder) {
                window.settingsFolder = folder
            } else {
                window.batchFolder = folder
            }
        }
    }
    FolderDialog {
        id: recoverDialog
        title: "换一个目录保存文稿"
        onAccepted: appState.retry(window.saveJob, window.localPath(selectedFolder))
    }
    Dialog {
        id: editDialog
        title: "编辑等待任务"
        anchors.centerIn: parent
        modal: true
        width: 515
        onOpened: { window.editError = ""; aliasField.forceActiveFocus() }
        ColumnLayout {
            width: editDialog.availableWidth
            spacing: 10
            Hint {
                Layout.fillWidth: true
                textFormat: Text.PlainText
                text: "原始标题：" + (window.editing.title || "")
                maximumLineCount: 4
                elide: Text.ElideRight
                ToolTip.visible: editTitleHover.hovered
                ToolTip.text: window.editing.title || ""
                HoverHandler { id: editTitleHover }
            }
            Hint {
                Layout.fillWidth: true
                textFormat: Text.PlainText
                text: window.editing.locator || ""
                visible: text !== ""
                wrapMode: Text.WrapAnywhere
                maximumLineCount: 2
                elide: Text.ElideMiddle
            }
            Label { text: "任务简称" }
            Field {
                id: aliasField
                objectName: "waitingAlias"
                Layout.fillWidth: true
                Accessible.name: "任务简称"
                maximumLength: 80
                placeholderText: window.jobTitle(window.editing)
            }
            Hint { text: "简称只用于界面显示。留空时自动截取标题。" }
            Label { text: "保存目录"; Layout.topMargin: 6 }
            RowLayout {
                Layout.fillWidth: true
                PathField { id: editOutput; Layout.fillWidth: true; Accessible.name: "任务保存目录" }
                Action { text: "浏览"; onClicked: { window.chooseEditingFolder = true; outputDialog.open() } }
            }
            Hint { text: window.editError; visible: text !== ""; color: window.accent; Layout.fillWidth: true }
            RowLayout {
                Layout.fillWidth: true
                ColumnLayout {
                    Layout.fillWidth: true
                    Label { text: "转写模型" }
                    Choice { id: editModel; Layout.fillWidth: true; model: window.modelLabels; Accessible.name: "任务模型" }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    Label { text: "导出格式" }
                    Choice { id: editFormat; Layout.fillWidth: true; model: window.formatLabels; Accessible.name: "任务导出格式" }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: 10
                Item { Layout.fillWidth: true }
                Action { text: "取消"; quiet: true; onClicked: editDialog.close() }
                Action {
                    objectName: "saveWaiting"
                    text: "保存任务"
                    primary: true
                    enabled: editOutput.text.trim() !== ""
                    onClicked: {
                        if (appState.editWaiting(window.editing.id, aliasField.text, editOutput.text,
                                                 window.models[editModel.currentIndex],
                                                 window.formats[editFormat.currentIndex]) !== false)
                            editDialog.close()
                    }
                }
            }
        }
    }
    Dialog {
        id: removeDialog
        title: "移除等待任务？"
        anchors.centerIn: parent
        modal: true
        width: 435
        ColumnLayout {
            width: removeDialog.availableWidth
            spacing: 12
            Heading { text: window.jobTitle(window.removing); Layout.fillWidth: true; elide: Text.ElideRight; textFormat: Text.PlainText }
            Hint { text: "任务将从队列移除，源文件会保留。" }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                Action { text: "取消"; quiet: true; onClicked: removeDialog.close() }
                Action {
                    objectName: "confirmRemoveWaiting"
                    text: "移除任务"
                    primary: true
                    onClicked: { appState.removeWaiting(window.removing.id); removeDialog.close() }
                }
            }
        }
    }
    Dialog {
        id: quitDialog
        title: "结束当前处理？"
        anchors.centerIn: parent
        modal: true
        implicitWidth: 410
        standardButtons: Dialog.Ok | Dialog.Cancel
        Hint {
            text: window.smallBusy ? "退出会停止正在进行的下载。已完成的文稿和等待任务会保留。"
                                   : "已完成的结果会保留，剩余项可在任务记录中继续。"
        }
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
            if (editDialog.visible) window.editError = value
            toastTimer.restart()
        }
        function onSettingsSaved() {
            window.settingsFolder = appState.settings.root
            if (appState.stage === 1) {
                window.batchFolder = appState.settings.root
                batchModel.currentIndex = window.models.indexOf(appState.settings.model)
                batchFormat.currentIndex = window.formats.indexOf(appState.settings.format)
            }
        }
    }
    Timer { id: toastTimer; interval: 8500; onTriggered: window.message = "" }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            Layout.fillHeight: true
            Layout.preferredWidth: window.split ? 196 : 164
            color: window.rail
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: window.split ? 20 : 15
                spacing: 6
                RowLayout {
                    Layout.fillWidth: true
                    Layout.topMargin: 15
                    Layout.bottomMargin: 44
                    spacing: 10
                    Image {
                        source: "assets/pianyu.svg"
                        sourceSize: Qt.size(88, 88)
                        Layout.preferredWidth: 44
                        Layout.preferredHeight: 44
                        fillMode: Image.PreserveAspectFit
                    }
                    ColumnLayout {
                        spacing: 0
                        Heading { text: "片语"; font.pixelSize: 27; color: window.railText }
                        Hint { text: "视频转文字"; font.pixelSize: 12; color: "#d2cdbf" }
                    }
                }
                Action {
                    Layout.fillWidth: true
                    objectName: "navWork"
                    onRail: true
                    navGlyph: "import"
                    text: "导入素材"
                    highlightedNav: window.page === "work"
                    onClicked: window.navigate("work")
                }
                Action {
                    Layout.fillWidth: true
                    objectName: "navQueue"
                    onRail: true
                    navGlyph: "queue"
                    text: "任务队列" + (window.queueJobs.length ? "  " + window.queueJobs.length : "")
                    highlightedNav: window.page === "queue" || (window.page === "result" && window.resultBack === "queue")
                    onClicked: window.navigate("queue")
                }
                Action {
                    Layout.fillWidth: true
                    objectName: "navHistory"
                    onRail: true
                    navGlyph: "history"
                    text: "任务记录"
                    highlightedNav: window.page === "history" || (window.page === "result" && window.resultBack === "history")
                    onClicked: window.navigate("history")
                }
                Action {
                    Layout.fillWidth: true
                    objectName: "navSettings"
                    onRail: true
                    navGlyph: "settings"
                    text: "设置"
                    highlightedNav: window.page === "settings"
                    onClicked: window.navigate("settings")
                }
                Item { Layout.fillHeight: true }
                RowLayout {
                    Layout.leftMargin: 12
                    Layout.bottomMargin: 8
                    spacing: 8
                    Rectangle { width: 6; height: 6; radius: 3; color: appState.running ? "#f4b19d" : "#a9ba9e" }
                    Hint { text: appState.running ? "正在本机处理" : "本机工作台"; font.pixelSize: 12; color: "#d2cdbf" }
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.leftMargin: window.split ? 42 : 24
            Layout.rightMargin: window.split ? 42 : 24
            Layout.topMargin: window.split ? 36 : 22
            Layout.bottomMargin: 24
            spacing: window.split ? 22 : 13
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 5
                RowLayout {
                    Layout.fillWidth: true
                    Heading {
                        Layout.fillWidth: true
                        text: ({work: appState.stage === 2 ? "确认素材" : "导入素材",
                                queue: "任务队列", result: "提取结果",
                                history: "任务记录", settings: "设置"})[window.page]
                        font.pixelSize: 37
                    }
                    Action {
                        visible: window.page === "result"
                        text: window.resultBack === "history" ? "返回任务记录" : "返回任务队列"
                        quiet: true
                        onClicked: window.page = window.resultBack
                    }
                }
                Hint {
                    Layout.fillWidth: true
                    text: ({work: appState.stage === 2 ? "选好素材和导出方式，再把任务加入等待队列。"
                                                   : "粘贴视频分享文字或本地路径，也可以从文件夹批量导入。",
                            queue: "等待任务可以改名、调整导出选项或移除。准备好后，再开始提取。",
                            result: "选择一条任务，查看处理状态或阅读已完成的文稿。",
                            history: "打开批次，查看已生成的文稿或继续未完成的任务。",
                            settings: "设置新任务的默认保存位置、转写模型和同时处理数。"})[window.page]
                }
            }

            StackLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                currentIndex: ({work: 0, result: 0, history: 1, settings: 2, queue: 3})[window.page]

                StackLayout {
                    currentIndex: window.page === "result" ? 2 : appState.stage === 2 ? 1 : 0

                    RowLayout {
                      spacing: 24
                      Surface {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.preferredWidth: 650
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: window.split ? 24 : 16
                            spacing: 15
                            Heading { text: "粘贴链接与路径" }
                            ScrollView {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                TextArea {
                                    id: input
                                    objectName: "sourceInput"
                                    text: window.draft
                                    onTextChanged: window.draft = text
                                    placeholderText: "粘贴分享文本、链接或本地文件路径\n例如 https://v.douyin.com/…\n或 D:\\视频素材\\采访.mp4"
                                    placeholderTextColor: window.muted
                                    color: window.ink
                                    selectionColor: window.accent
                                    selectedTextColor: window.ivory
                                    Accessible.name: "视频链接与本地路径"
                                    wrapMode: TextEdit.Wrap
                                    selectByMouse: true
                                    padding: 16
                                    background: Rectangle {
                                        color: window.inset; radius: 10
                                        border.width: input.activeFocus ? 2 : 0
                                        border.color: window.accent
                                    }
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Action { text: "选择文件夹"; enabled: !window.parsing; onClicked: sourceDialog.open() }
                                Hint { text: "包括子文件夹"; Layout.fillWidth: true }
                                Action {
                                    objectName: "resolveMaterials"
                                    text: window.parsing ? "正在解析…" : "解析素材"
                                    primary: true
                                    enabled: !window.parsing && (window.draft.trim() !== "" || appState.folders.length > 0)
                                    onClicked: {
                                        window.batchFolder = appState.settings.root
                                        batchOutput.text = appState.settings.root
                                        batchModel.currentIndex = window.models.indexOf(appState.settings.model)
                                        batchFormat.currentIndex = window.formats.indexOf(appState.settings.format)
                                        appState.resolve(input.text)
                                    }
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
                                        enabled: !window.parsing
                                        onClicked: appState.removeFolder(modelData)
                                    }
                                }
                            }
                        }
                      }
                      ColumnLayout {
                        visible: window.split
                        Layout.alignment: Qt.AlignTop
                        Layout.preferredWidth: 233
                        Layout.maximumWidth: 233
                        spacing: 14
                        Surface {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 190
                            color: window.leafWash
                            PaperSprite {
                                anchors.fill: parent
                                anchors.margins: 9
                                motion: window.motion && visible
                                listening: window.parsing
                            }
                        }
                        Heading {
                            text: window.parsing ? "纸灵正在听" : "从声音到文稿"
                            font.pixelSize: 21
                            Layout.leftMargin: 8
                        }
                        Hint {
                            text: "优先使用可用的中文字幕。需要语音识别时，片语会在本机转写。"
                            Layout.fillWidth: true
                            Layout.leftMargin: 8
                        }
                        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: window.border }
                        Hint { text: "抖音、B 站、本地音视频\n支持导出 TXT 与 SRT"; Layout.leftMargin: 8 }
                      }
                    }

                    Surface {
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 16
                            spacing: 9
                            RowLayout {
                                Layout.fillWidth: true
                                Heading { text: "已解析素材" }
                                Item { Layout.fillWidth: true }
                                Action {
                                    objectName: "selectAllMaterials"
                                    text: "全选可用 / 清除全选"
                                    enabled: !window.parsing
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
                                color: window.inset
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
                                    color: modelData.checked ? window.leafWash : materialHover.hovered ? "#f2f1e6" : window.ivory
                                    Behavior on color { enabled: window.motion; ColorAnimation { duration: 120 } }
                                    HoverHandler { id: materialHover }
                                    Rectangle { height: 1; width: parent.width; anchors.bottom: parent.bottom; color: window.border }
                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.leftMargin: 4
                                        anchors.rightMargin: 9
                                        CheckBox {
                                            Accessible.name: materialRow.modelData.title
                                            enabled: !materialRow.modelData.error && !window.parsing
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
                                PathField { id: batchOutput; objectName: "batchOutput"; Layout.fillWidth: true; text: window.batchFolder
                                    selectByMouse: true; onTextEdited: window.batchFolder = text }
                                Action { text: "浏览"; onClicked: { window.chooseEditingFolder = false; window.chooseSettingFolder = false; outputDialog.open() } }
                                Hint { text: "中文 · 简体输出"; font.pixelSize: 12 }
                                Label { text: "转写模型"; color: window.ink }
                                RowLayout {
                                    Choice { id: batchModel; model: window.modelLabels; Layout.preferredWidth: 166
                                        currentIndex: window.models.indexOf(appState.settings.model) }
                                    Hint {
                                        font.pixelSize: 12
                                        text: appState.modelsInfo[window.models[batchModel.currentIndex]]?.ready ?
                                                  "已缓存" : "首次转写时下载 " +
                                                  (appState.modelsInfo[window.models[batchModel.currentIndex]]?.size || "")
                                    }
                                }
                                Label { text: "导出格式"; color: window.ink }
                                Choice { id: batchFormat; model: window.formatLabels; Layout.preferredWidth: 161
                                    currentIndex: window.formats.indexOf(appState.settings.format) }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Action { text: "返回导入"; quiet: true; enabled: !window.parsing; onClicked: appState.back() }
                                Item { Layout.fillWidth: true }
                                Hint { text: "已选 " + appState.selectedCount + " 条" }
                                Action {
                                    objectName: "submitBatch"
                                    primary: true
                                    enabled: appState.selectedCount > 0 && !window.parsing
                                    text: "加入等待队列"
                                    onClicked: {
                                        if (appState.enqueue(batchOutput.text, window.models[batchModel.currentIndex],
                                                             window.formats[batchFormat.currentIndex]) !== false) {
                                            window.draft = ""
                                            window.page = "queue"
                                        }
                                    }
                                }
                            }
                        }
                    }

                    ColumnLayout {
                        spacing: 9
                        RowLayout {
                            Layout.fillWidth: true
                            Heading { text: "已完成 " + window.completed + " / " + appState.jobs.length }
                            Meter { Layout.fillWidth: true; from: 0; to: Math.max(1, appState.jobs.length); value: window.completed; Accessible.name: "批次完成进度" }
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
                                Layout.minimumHeight: window.split ? 0 : 102
                                Layout.maximumHeight: window.split ? Infinity : 119
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
                                                color: appState.detail.id === jobRow.modelData.id ? window.leafWash :
                                                           jobRow.hovered ? window.inset : window.ivory
                                                radius: 6
                                                border.width: jobRow.visualFocus ? 2 : 0
                                                border.color: window.accent
                                            }
                                            ToolTip.visible: hovered && modelData.title !== window.jobTitle(modelData)
                                            ToolTip.delay: 650
                                            ToolTip.text: modelData.title || ""
                                            contentItem: RowLayout {
                                                spacing: 6
                                                ColumnLayout {
                                                    Layout.fillWidth: true
                                                    spacing: 1
                                                    Label { Layout.fillWidth: true; elide: Text.ElideRight; color: window.ink; textFormat: Text.PlainText
                                                        text: window.jobTitle(jobRow.modelData) }
                                                    Hint { Layout.fillWidth: true; font.pixelSize: 12; wrapMode: Text.NoWrap
                                                        elide: Text.ElideRight; textFormat: Text.PlainText
                                                        text: jobRow.modelData.error || window.progressLabel(jobRow.modelData) || jobRow.modelData.source }
                                                    Meter { Layout.fillWidth: true
                                                        flowing: true
                                                        visible: window.measured(jobRow.modelData)
                                                        value: visible ? jobRow.modelData.progress : 0
                                                        Accessible.name: jobRow.modelData.stateLabel + "进度" }
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
                                Layout.minimumHeight: window.split ? 0 : 224
                                ColumnLayout {
                                    anchors.fill: parent
                                    anchors.margins: 12
                                    spacing: 6
                                    Heading { text: "文案预览" }
                                    Label {
                                        Layout.fillWidth: true
                                        text: window.jobTitle(appState.detail) || "选择一条任务"
                                        color: window.ink
                                        textFormat: Text.PlainText
                                        elide: Text.ElideRight
                                        font.bold: true
                                    }
                                    Hint {
                                        Layout.fillWidth: true
                                        visible: Boolean(appState.detail.title) && appState.detail.title !== window.jobTitle(appState.detail)
                                        text: "原始标题：" + (appState.detail.title || "")
                                        textFormat: Text.PlainText
                                        maximumLineCount: 3
                                        elide: Text.ElideRight
                                        ToolTip.visible: originalHover.hovered
                                        ToolTip.delay: 650
                                        ToolTip.text: appState.detail.title || ""
                                        HoverHandler { id: originalHover }
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
                                        Layout.minimumHeight: 70
                                        TextArea {
                                            readOnly: true
                                            selectByMouse: true
                                            wrapMode: TextEdit.Wrap
                                            padding: 8
                                            text: window.timeline && appState.detail.timeline ?
                                                      appState.detail.timeline.map(function(row) { return row.stamp + "   " + row.text }).join("\n") :
                                                      appState.detail.text || ""
                                            background: Rectangle { radius: 6; color: window.inset }
                                        }
                                    }
                                    Flow {
                                        Layout.fillWidth: true
                                        spacing: 6
                                        Action {
                                            objectName: "copyResult"
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
                                objectName: "historyBatch_" + modelData.id
                                width: ListView.view.width - 6
                                height: 54
                                text: "批次 " + modelData.id + "    " + modelData.created.slice(0, 16).replace("T", " ") +
                                      "    完成 " + modelData.done + " / " + modelData.total + "    待处理或失败 " + modelData.failed
                                onClicked: window.openResult(record.modelData.id, 0, "history")
                            }
                        }
                    }
                }

                Surface {
                    id: settingsPane
                    readonly property bool checking: appState.folderCheck.status === "checking"
                    readonly property bool matchesCheck: appState.folderCheck.path === defaultOutput.text.trim()
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: window.split ? 24 : 17
                        spacing: 15
                        ScrollView {
                            id: settingsScroll
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true
                            contentWidth: availableWidth
                            GridLayout {
                                width: settingsScroll.availableWidth
                                columns: window.split ? 2 : 1
                                columnSpacing: 42
                                rowSpacing: 30
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 445
                                    Layout.alignment: Qt.AlignTop
                                    spacing: 12
                                    Heading { text: "文件保存"; font.pixelSize: 22 }
                                    Hint { Layout.fillWidth: true; text: "每条文稿按原始视频标题单独归档。" }
                                    PathField {
                                        id: defaultOutput
                                        objectName: "settingsOutput"
                                        Layout.fillWidth: true
                                        enabled: !settingsPane.checking
                                        text: window.settingsFolder
                                        Accessible.name: "默认保存目录"
                                        onTextEdited: window.settingsFolder = text
                                    }
                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: 6
                                        Action {
                                            text: "选择目录"
                                            enabled: !settingsPane.checking
                                            onClicked: { window.chooseEditingFolder = false; window.chooseSettingFolder = true; outputDialog.open() }
                                        }
                                        Action {
                                            text: "校验"
                                            objectName: "checkOutput"
                                            enabled: !settingsPane.checking && defaultOutput.text.trim() !== ""
                                            onClicked: appState.checkFolder(defaultOutput.text)
                                        }
                                        Action {
                                            text: "打开"
                                            objectName: "openSettingsOutput"
                                            quiet: true
                                            enabled: !settingsPane.checking && defaultOutput.text.trim() !== ""
                                            onClicked: appState.openFolder(defaultOutput.text)
                                        }
                                        Item { Layout.fillWidth: true }
                                        Hint { text: "Ctrl + 单击路径也可打开"; font.pixelSize: 12 }
                                    }
                                    Hint {
                                        objectName: "folderCheckMessage"
                                        Layout.fillWidth: true
                                        Layout.minimumHeight: 23
                                        text: settingsPane.matchesCheck && appState.folderCheck.message ? appState.folderCheck.message :
                                              "保存时会校验写入权限，并创建尚不存在的目录。"
                                        color: settingsPane.matchesCheck && appState.folderCheck.status === "invalid" ? window.accent :
                                               settingsPane.matchesCheck && appState.folderCheck.status === "valid" ? window.leaf : window.muted
                                    }
                                    RowLayout {
                                        Layout.fillWidth: true
                                        Layout.topMargin: 2
                                        Label { text: "默认导出格式"; Layout.fillWidth: true }
                                        Choice {
                                            id: defaultFormat
                                            objectName: "defaultFormat"
                                            model: window.formatLabels
                                            Layout.preferredWidth: 166
                                            currentIndex: window.formats.indexOf(appState.settings.format)
                                            Accessible.name: "默认导出格式"
                                        }
                                    }
                                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; Layout.topMargin: 12; color: window.border }
                                    RowLayout {
                                        Layout.fillWidth: true
                                        ColumnLayout {
                                            Layout.fillWidth: true
                                            spacing: 3
                                            Heading { text: "减少动态效果"; font.pixelSize: 16 }
                                            Hint { Layout.fillWidth: true; text: "关闭涟漪和流动动画，保留状态提示。" }
                                        }
                                        Switch {
                                            id: reducedMotion
                                            objectName: "reducedMotion"
                                            checked: appState.settings.reducedMotion
                                            Accessible.name: "减少动态效果"
                                            onToggled: appState.setReducedMotion(checked)
                                            indicator: Rectangle {
                                                x: reducedMotion.leftPadding
                                                y: (reducedMotion.height - height) / 2
                                                implicitWidth: 43
                                                implicitHeight: 24
                                                radius: 12
                                                color: reducedMotion.checked ? window.leaf : "#deded1"
                                                border.width: reducedMotion.visualFocus ? 2 : 0
                                                border.color: window.accent
                                                Rectangle {
                                                    width: 18; height: 18; radius: 9
                                                    x: reducedMotion.checked ? parent.width - width - 3 : 3
                                                    y: 3; color: window.ivory
                                                }
                                            }
                                        }
                                    }
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 350
                                    Layout.alignment: Qt.AlignTop
                                    spacing: 12
                                    Heading { text: "转写模型"; font.pixelSize: 22 }
                                    Surface {
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: smallCard.implicitHeight + 28
                                        color: window.leafWash
                                        ColumnLayout {
                                            id: smallCard
                                            anchors.fill: parent
                                            anchors.margins: 14
                                            spacing: 8
                                            RowLayout {
                                                Layout.fillWidth: true
                                                Heading { Layout.fillWidth: true; text: "快速 · small"; font.pixelSize: 17 }
                                                Status {
                                                    label: appState.modelsInfo.small.ready ? "已缓存" : window.smallBusy ? "下载中" : appState.modelsInfo.small.size
                                                    status: appState.modelsInfo.small.ready ? "done" : ""
                                                }
                                            }
                                            Hint { Layout.fillWidth: true; text: "适合清晰口播和批量初稿。提前下载后，处理本地素材时可离线使用。" }
                                            RowLayout {
                                                Layout.fillWidth: true
                                                Hint {
                                                    id: downloadHint
                                                    objectName: "smallDownloadMessage"
                                                    Layout.fillWidth: true
                                                    text: appState.modelsInfo.small.ready && !window.smallBusy ? "本机模型已就绪，可离线使用" :
                                                          appState.smallDownload.message || "下载到本机模型缓存"
                                                    color: appState.smallDownload.status === "failed" && !appState.modelsInfo.small.ready ? window.accent : window.leaf
                                                    font.pixelSize: 12
                                                }
                                                Action {
                                                    visible: !appState.modelsInfo.small.ready || window.smallBusy
                                                    objectName: "downloadSmall"
                                                    text: appState.smallDownload.status === "stopping" ? "正在停止" : window.smallBusy ? "停止" :
                                                          appState.smallDownload.status === "failed" ? "重试下载" : "提前下载"
                                                    primary: !window.smallBusy
                                                    enabled: appState.smallDownload.status !== "stopping"
                                                    onClicked: window.smallBusy ? appState.cancelSmallDownload() : appState.downloadSmall()
                                                }
                                            }
                                            RowLayout {
                                                Layout.fillWidth: true
                                                visible: window.smallBusy
                                                Meter {
                                                    Layout.fillWidth: true
                                                    implicitHeight: 7
                                                    indeterminate: typeof appState.smallDownload.progress !== "number"
                                                    value: indeterminate ? 0 : appState.smallDownload.progress / 100
                                                    flowing: appState.smallDownload.status === "downloading"
                                                    Accessible.name: "small 下载进度"
                                                }
                                                Label {
                                                    objectName: "smallDownloadPercentage"
                                                    text: typeof appState.smallDownload.progress === "number" ? appState.smallDownload.progress + "%" : ""
                                                    color: window.leaf
                                                    font.pixelSize: 12
                                                    Accessible.name: text
                                                }
                                            }
                                        }
                                    }
                                    RowLayout {
                                        Layout.fillWidth: true
                                        Heading { Layout.fillWidth: true; text: "均衡 · medium"; font.pixelSize: 16 }
                                        Hint { text: appState.modelsInfo.medium.ready ? "已缓存" : appState.modelsInfo.medium.size }
                                    }
                                    Hint { Layout.fillWidth: true; text: "常规中文口播默认使用。需要时自动下载。" }
                                    GridLayout {
                                        Layout.fillWidth: true
                                        Layout.topMargin: 8
                                        columns: 2
                                        rowSpacing: 9
                                        Label { Layout.fillWidth: true; text: "默认模型" }
                                        Choice {
                                            id: defaultModel
                                            model: window.modelLabels
                                            Layout.preferredWidth: 166
                                            currentIndex: window.models.indexOf(appState.settings.model)
                                            Accessible.name: "默认模型"
                                        }
                                        Label { Layout.fillWidth: true; text: "同时处理数" }
                                        Choice {
                                            id: parallelism
                                            objectName: "parallelism"
                                            model: ["1 条（默认）", "2 条", "3 条"]
                                            currentIndex: (appState.settings.parallelism || 1) - 1
                                            Layout.preferredWidth: 166
                                            Accessible.name: "同时处理数"
                                        }
                                    }
                                }
                            }
                        }
                        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: window.border }
                        RowLayout {
                            Layout.fillWidth: true
                            Hint { Layout.fillWidth: true; text: "导出与并发设置将用于新任务。"; font.pixelSize: 12 }
                            Action {
                                text: settingsPane.checking ? "校验中…" : "保存设置"
                                primary: true
                                objectName: "saveSettings"
                                enabled: !settingsPane.checking && defaultOutput.text.trim() !== ""
                                onClicked: appState.saveSettings(defaultOutput.text, window.models[defaultModel.currentIndex],
                                                                 window.formats[defaultFormat.currentIndex], parallelism.currentIndex + 1)
                            }
                        }
                    }
                }
                ColumnLayout {
                    spacing: 12
                    RowLayout {
                        Layout.fillWidth: true
                        Hint {
                            Layout.fillWidth: true
                            text: "已启动 " + appState.activeJobs.length + " · 等待 " + appState.waitingJobs.length
                                  + " · 同时处理上限 " + (appState.settings.parallelism || 1)
                        }
                        Action {
                            visible: appState.history.length > 0
                            text: "查看任务记录"; quiet: true; onClicked: window.navigate("history")
                        }
                        Action {
                            objectName: "startAllWaiting"
                            text: "开始全部等待任务"
                            primary: true
                            enabled: appState.waitingJobs.length > 0
                            onClicked: { window.message = ""; appState.startWaiting(0) }
                        }
                    }
                    Surface {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        ColumnLayout {
                            anchors.centerIn: parent
                            visible: !window.queueJobs.length
                            spacing: 10
                            Heading { text: "队列为空"; font.pixelSize: 23 }
                            Hint { text: "导入并确认素材后，任务会在这里等待开始。" }
                            Action {
                                text: appState.history.length ? "查看任务记录" : "导入素材"
                                onClicked: window.navigate(appState.history.length ? "history" : "work")
                            }
                        }
                        ListView {
                            anchors.fill: parent
                            anchors.margins: 14
                            clip: true
                            model: queueModel
                            ScrollBar.vertical: ScrollBar {}
                            delegate: ColumnLayout {
                                id: queueRow
                                required property int index
                                required property int jobId
                                readonly property var modelData: window.queueIndex[jobId] || ({})
                                readonly property bool waiting: modelData.status === "waiting"
                                readonly property bool groupStart: index === 0 || index === appState.activeJobs.length
                                width: ListView.view.width - 7
                                spacing: 0
                                Heading {
                                    visible: queueRow.groupStart
                                    Layout.leftMargin: 9
                                    Layout.topMargin: 5
                                    Layout.bottomMargin: 5
                                    text: queueRow.waiting ? "等待启动" : "已启动"
                                    font.pixelSize: 16
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    Layout.minimumHeight: 66
                                    Layout.leftMargin: 9
                                    Layout.rightMargin: 5
                                    spacing: window.split ? 14 : 8
                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 2
                                        Label {
                                            Layout.fillWidth: true
                                            text: window.jobTitle(queueRow.modelData)
                                            textFormat: Text.PlainText
                                            color: window.ink
                                            font.weight: Font.DemiBold
                                            elide: Text.ElideRight
                                            ToolTip.visible: titleHover.hovered
                                            ToolTip.delay: 550
                                            ToolTip.text: queueRow.modelData.title || ""
                                            HoverHandler { id: titleHover }
                                        }
                                        Hint {
                                            Layout.fillWidth: true
                                            text: queueRow.modelData.source + " · " + window.duration(queueRow.modelData.duration)
                                            wrapMode: Text.NoWrap
                                            elide: Text.ElideRight
                                            font.pixelSize: 12
                                        }
                                    }
                                    ColumnLayout {
                                        Layout.preferredWidth: 174
                                        spacing: 4
                                        visible: !queueRow.waiting
                                        Hint {
                                            objectName: "activeStatus_" + queueRow.jobId
                                            Layout.fillWidth: true
                                            wrapMode: Text.NoWrap
                                            text: (queueRow.modelData.status === "queued" ? "等待可用名额" : queueRow.modelData.stateLabel || "处理中") +
                                                  (window.measured(queueRow.modelData) ? " · " + window.progressLabel(queueRow.modelData) : "")
                                            elide: Text.ElideRight
                                            font.pixelSize: 12
                                            ToolTip.visible: statusHover.hovered
                                            ToolTip.text: text
                                            HoverHandler { id: statusHover }
                                        }
                                        Meter {
                                            Layout.fillWidth: true
                                            flowing: true
                                            visible: window.measured(queueRow.modelData)
                                            value: visible ? queueRow.modelData.progress : 0
                                            Accessible.name: (queueRow.modelData.stateLabel || "处理") + "进度"
                                        }
                                    }
                                    Action {
                                        visible: queueRow.waiting
                                        objectName: "editWaiting_" + queueRow.modelData.id
                                        text: "编辑"
                                        quiet: true
                                        onClicked: {
                                            window.editing = queueRow.modelData
                                            aliasField.text = window.editing.alias || ""
                                            editOutput.text = window.editing.root
                                            editModel.currentIndex = window.models.indexOf(window.editing.model)
                                            editFormat.currentIndex = window.formats.indexOf(window.editing.format)
                                            editDialog.open()
                                        }
                                    }
                                    Action {
                                        visible: queueRow.waiting
                                        objectName: "removeWaiting_" + queueRow.modelData.id
                                        text: "移除"
                                        quiet: true
                                        onClicked: { window.removing = queueRow.modelData; removeDialog.open() }
                                    }
                                    Action {
                                        visible: queueRow.waiting
                                        objectName: "startWaiting_" + queueRow.modelData.id
                                        text: "开始"
                                        onClicked: { window.message = ""; appState.startWaiting(queueRow.modelData.id) }
                                    }
                                    Action {
                                        visible: !queueRow.waiting
                                        text: "查看"
                                        quiet: true
                                        onClicked: window.openResult(queueRow.modelData.batch, queueRow.modelData.id, "queue")
                                    }
                                    Action {
                                        visible: !queueRow.waiting
                                        text: "取消"
                                        quiet: true
                                        enabled: !queueRow.modelData.stopping
                                        onClicked: appState.cancelJob(queueRow.modelData.id)
                                    }
                                }
                                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: window.border }
                            }
                        }
                    }
                }
            }
            Surface {
                Layout.fillWidth: true
                Layout.preferredHeight: toast.implicitHeight + 13
                visible: window.message !== ""
                color: window.blush
                Hint { id: toast; anchors.left: parent.left; anchors.right: parent.right
                    anchors.margins: 7; anchors.verticalCenter: parent.verticalCenter; text: window.message }
            }
        }
    }
}
