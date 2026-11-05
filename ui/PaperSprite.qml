import QtQuick

Item {
    id: sprite
    property bool motion: true
    property bool listening: false
    Accessible.ignored: true

    Item {
        id: artwork
        anchors.centerIn: parent
        width: 240
        height: 176
        scale: Math.min(sprite.width / width, sprite.height / height)
        transform: Translate { id: lift; y: 0 }
        Image {
            anchors.fill: parent
            source: "assets/paper-sprite.svg"
            sourceSize: Qt.size(480, 352)
            smooth: true
        }
        Row {
            x: 99 + (sprite.motion && presence.hovered ? (presence.point.position.x / sprite.width - 0.5) * 7 : 0)
            y: 76
            spacing: 24
            scale: 1
            Behavior on x { enabled: sprite.motion; NumberAnimation { duration: 125; easing.type: Easing.OutCubic } }
            Repeater {
                model: 2
                Rectangle {
                    width: 9
                    height: sprite.listening ? 5 : 13
                    y: (13 - height) / 2
                    radius: 5
                    color: "#314d3d"
                    Behavior on height { enabled: sprite.motion; NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
                }
            }
        }
    }
    HoverHandler {
        id: presence
        onHoveredChanged: {
            if (hovered && sprite.motion)
                nod.restart()
        }
    }
    onListeningChanged: if (listening && motion) nod.restart()
    onMotionChanged: if (!motion) { nod.stop(); lift.y = 0 }
    SequentialAnimation {
        id: nod
        NumberAnimation { target: lift; property: "y"; to: -5; duration: 125; easing.type: Easing.OutCubic }
        NumberAnimation { target: lift; property: "y"; to: 0; duration: 180; easing.type: Easing.OutCubic }
    }
}
