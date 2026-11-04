# -*- coding: utf-8 -*-

# Main window layout and styling. Started life as pyuic output from Template.ui
# but is hand-maintained now: edit this file directly and never regenerate it.
#
# Font sizes live in the stylesheet in px, with no font family, so the app uses
# each OS's own UI font at the same size. Point sizes differ per platform dpi.


from PyQt6 import QtCore, QtGui, QtWidgets

STYLESHEET = """
QFrame#frame {
    background-color: qlineargradient(spread:pad, x1:1.072045, y1:0.124, x2:0.368, y2:1,
        stop:0.06 rgba(80, 214, 255, 255), stop:0.72 rgba(112, 32, 213, 255));
    border-radius: 16px;
}
QFrame#SONGINFORMATION { background-color: #141415; border-radius: 16px; }

#frame QLabel, #SONGINFORMATION QLabel { color: #FFFFFF; background: transparent; }

QLabel#title { font-size: 28px; font-weight: 600; }
QLabel#version {
    font-size: 10px;
    font-weight: 500;
    padding: 0 7px;
    border-radius: 8px;
    color: rgba(255, 255, 255, 0.88);
    background-color: rgba(255, 255, 255, 0.16);
}
QLabel#author { font-size: 12px; color: rgba(255, 255, 255, 0.88); }

QPushButton#Closed, QPushButton#SettingsBtn {
    background-color: rgba(22, 10, 64, 0.32);
    border: none;
    border-radius: 8px;
}
QPushButton#SettingsBtn:hover { background-color: rgba(22, 10, 64, 0.55); }
QPushButton#Closed:hover { background-color: rgba(226, 52, 88, 0.95); }
QPushButton#SettingsBtn:pressed, QPushButton#Closed:pressed {
    background-color: rgba(22, 10, 64, 0.75);
}

QLineEdit#PlaylistLink {
    background-color: rgba(255, 255, 255, 0.96);
    border: 2px solid transparent;
    border-radius: 10px;
    color: #15151F;
    font-size: 13px;
    padding: 0 10px;
    selection-background-color: #7020D5;
    selection-color: #FFFFFF;
    placeholder-text-color: #6E6E82;
}
QLineEdit#PlaylistLink:focus { border-color: #1ED760; }

QPushButton#DownloadBtn {
    background-color: #1ED760;
    border: 2px solid transparent;
    border-radius: 10px;
    color: #06240F;
    font-size: 15px;
    font-weight: 600;
}
QPushButton#DownloadBtn:hover { background-color: #3BE477; }
QPushButton#DownloadBtn:pressed { background-color: #18B650; }
QPushButton#DownloadBtn:focus { border-color: #FFFFFF; }
QPushButton#DownloadBtn[running="true"] {
    background-color: #B3123A;
    color: #FFFFFF;
}
QPushButton#DownloadBtn[running="true"]:hover { background-color: #C81E48; }
QPushButton#DownloadBtn[running="true"]:pressed { background-color: #96102F; }
QPushButton#DownloadBtn:disabled {
    background-color: rgba(255, 255, 255, 0.45);
    color: rgba(21, 21, 31, 0.6);
}

QFrame#activityCard {
    background-color: rgba(22, 10, 64, 0.34);
    border-radius: 12px;
}
QLabel#idleTitle { font-size: 16px; font-weight: 500; }
QLabel#idleHint { font-size: 12px; color: rgba(255, 255, 255, 0.88); }
QLabel#playlistCaption, QLabel#PlaylistMsg_2 {
    font-size: 11px;
    color: rgba(255, 255, 255, 0.72);
}
QLabel#AlbumName, QLabel#MainSongName { font-size: 13px; font-weight: 500; }
QLabel#CounterLabel { font-size: 12px; font-weight: 500; color: #7DF2A8; }
QLabel#CounterLabel[warn="true"] { color: #FFC1CC; }
QPushButton#ReportBtn {
    background-color: rgba(255, 255, 255, 0.16);
    border: none;
    border-radius: 6px;
    color: #FFFFFF;
    font-size: 11px;
    font-weight: 500;
    padding: 0 8px;
}
QPushButton#ReportBtn:hover { background-color: rgba(255, 255, 255, 0.28); }
QPushButton#ReportBtn:pressed { background-color: rgba(255, 255, 255, 0.1); }
QPushButton#ReportBtn:focus { border: 1px solid #FFFFFF; }
QLabel#statusMsg { font-size: 12px; color: rgba(255, 255, 255, 0.88); }

QProgressBar#SongDownloadprogress {
    background-color: rgba(255, 255, 255, 0.2);
    border: none;
    border-radius: 2px;
}
QProgressBar#SongDownloadprogress::chunk {
    background-color: #1ED760;
    border-radius: 2px;
}

QPushButton#Select_Home {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
    color: #FFFFFF;
    font-size: 12px;
    font-weight: 500;
}
QPushButton#Select_Home:hover { background-color: rgba(255, 255, 255, 0.14); }
QPushButton#Select_Home:pressed { background-color: rgba(255, 255, 255, 0.08); }
QPushButton#Select_Home:focus { border-color: #FFFFFF; }

#SONGINFORMATION QLabel#label_3 { font-size: 12px; color: #85858B; }
#SONGINFORMATION QLabel#SongName { font-size: 20px; font-weight: 500; color: #EDEDED; }
#SONGINFORMATION QLabel#ArtistNameText { font-size: 13px; color: #A1A1A6; }
#SONGINFORMATION QLabel#YearText { font-size: 12px; color: #85858B; }
"""


def _record_pixmap(size):
    """A vinyl record outline in white: this app's empty-state mark, drawn so it
    looks the same on every OS (the music-note glyph depends on the font)."""
    ratio = 3
    pixmap = QtGui.QPixmap(size * ratio, size * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 190), 1.5))
    center = QtCore.QPointF(size / 2, size / 2)
    for fraction in (0.46, 0.16):
        painter.drawEllipse(center, size * fraction, size * fraction)
    painter.end()
    return pixmap


def _line_icon(kind):
    """White header icon, drawn here so it looks the same on every OS (the
    gear glyph renders as a tiny or emoji-style symbol depending on the font)."""
    size, ratio = 16, 3
    pixmap = QtGui.QPixmap(size * ratio, size * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    white = QtGui.QColor("#FFFFFF")
    if kind == "close":
        pen = QtGui.QPen(white, 1.8, cap=QtCore.Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QtCore.QPointF(4.5, 4.5), QtCore.QPointF(11.5, 11.5))
        painter.drawLine(QtCore.QPointF(11.5, 4.5), QtCore.QPointF(4.5, 11.5))
    else:
        painter.translate(8, 8)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(white)
        for _ in range(8):
            painter.drawRoundedRect(QtCore.QRectF(-1.3, -7.2, 2.6, 3.4), 0.7, 0.7)
            painter.rotate(45)
        ring = QtGui.QPainterPath()
        ring.setFillRule(QtCore.Qt.FillRule.OddEvenFill)
        ring.addEllipse(QtCore.QPointF(0, 0), 5.2, 5.2)
        ring.addEllipse(QtCore.QPointF(0, 0), 2.1, 2.1)
        painter.drawPath(ring)
    painter.end()
    return QtGui.QIcon(pixmap)


class ToggleSwitch(QtWidgets.QCheckBox):
    """A QCheckBox (same signals and state) painted as a switch."""

    _TRACK_W, _TRACK_H, _GAP = 32, 18, 8

    def __init__(self, parent=None):
        super().__init__(parent)
        font = self.font()
        font.setPixelSize(13)
        font.setWeight(QtGui.QFont.Weight.Medium)
        self.setFont(font)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._knob = 0.0
        # tuned for the gradient card; set_colors() re-tints it for plain dialogs
        self._off = QtGui.QColor(22, 10, 64, 110)
        self._ring = QtGui.QColor(255, 255, 255, 110)
        self._focus = QtGui.QColor(255, 255, 255, 215)
        self._anim = QtCore.QVariantAnimation(self, duration=120)
        self._anim.valueChanged.connect(self._on_anim)
        self.toggled.connect(self._animate_to)

    def set_colors(self, off, ring, focus):
        self._off, self._ring, self._focus = off, ring, focus
        self.update()

    def _on_anim(self, value):
        self._knob = value
        self.update()

    def _animate_to(self, checked):
        self._anim.stop()
        self._anim.setStartValue(self._knob)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def sizeHint(self):
        if not self.text():
            return QtCore.QSize(self._TRACK_W + 2, 24)
        text_w = self.fontMetrics().horizontalAdvance(self.text())
        return QtCore.QSize(self._TRACK_W + self._GAP + text_w + 2, 24)

    def minimumSizeHint(self):
        return self.sizeHint()

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        top = (self.height() - self._TRACK_H) / 2
        track = QtCore.QRectF(0, top, self._TRACK_W, self._TRACK_H)
        off, on = self._off, QtGui.QColor("#1ED760")
        mix = self._knob
        color = QtGui.QColor(
            int(off.red() + (on.red() - off.red()) * mix),
            int(off.green() + (on.green() - off.green()) * mix),
            int(off.blue() + (on.blue() - off.blue()) * mix),
            int(off.alpha() + (on.alpha() - off.alpha()) * mix),
        )
        painter.setPen(QtGui.QPen(self._focus if self.hasFocus() else self._ring, 1))
        painter.setBrush(color)
        radius = self._TRACK_H / 2
        painter.drawRoundedRect(track, radius, radius)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor("#FFFFFF"))
        knob_x = 2 + (self._TRACK_W - self._TRACK_H) * mix
        painter.drawEllipse(QtCore.QRectF(knob_x, top + 2, self._TRACK_H - 4, self._TRACK_H - 4))
        if not self.text():
            return
        painter.setPen(QtGui.QColor("#FFFFFF"))
        text_rect = self.rect().adjusted(self._TRACK_W + self._GAP, 0, 0, 0)
        painter.drawText(
            text_rect,
            QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignLeft,
            self.text(),
        )


class CoverArt(QtWidgets.QLabel):
    """Album cover with rounded corners, or a music note before the first song."""

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHints(
            QtGui.QPainter.RenderHint.Antialiasing | QtGui.QPainter.RenderHint.SmoothPixmapTransform
        )
        rect = QtCore.QRectF(self.rect())
        path = QtGui.QPainterPath()
        path.addRoundedRect(rect, 12, 12)
        painter.setClipPath(path)
        pixmap = self.pixmap()
        if pixmap is None or pixmap.isNull():
            self._paint_record(painter, rect)
            return
        scaled = pixmap.scaled(
            self.size(),
            QtCore.Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        painter.drawPixmap(
            (self.width() - scaled.width()) // 2, (self.height() - scaled.height()) // 2, scaled
        )


    @staticmethod
    def _paint_record(painter, rect):
        """No cover yet: a record on an empty sleeve, grooves and a green label."""
        painter.fillRect(rect, QtGui.QColor("#1C1C1E"))
        center = rect.center()
        radius = rect.width() * 0.4
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor("#0F0F10"))
        painter.drawEllipse(center, radius, radius)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 14), 1))
        for fraction in (0.92, 0.8, 0.68, 0.56, 0.44):
            painter.drawEllipse(center, radius * fraction, radius * fraction)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor("#1ED760"))
        painter.drawEllipse(center, radius * 0.3, radius * 0.3)
        painter.setBrush(QtGui.QColor("#1C1C1E"))
        painter.drawEllipse(center, radius * 0.05, radius * 0.05)


class StatusLabel(QtWidgets.QLabel):
    """Two-line status text; anything longer stays readable in the tooltip.
    Hidden while empty so the idle hint stays centred in the card."""

    def setText(self, text):
        super().setText(text)
        self.setToolTip(text)
        self.setHidden(not text)


def _plain_label(parent, name, wrap=False):
    label = QtWidgets.QLabel(parent)
    label.setObjectName(name)
    # song and playlist names are user data: never let Qt parse them as html
    label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
    label.setWordWrap(wrap)
    return label


class Ui_MainWindow(object):
    def setupUi(self, MainWindow):
        MainWindow.setObjectName("MainWindow")
        MainWindow.resize(750, 540)
        MainWindow.setStyleSheet(STYLESHEET)
        self.centralwidget = QtWidgets.QWidget(MainWindow)
        self.centralwidget.setObjectName("centralwidget")

        self.frame = QtWidgets.QFrame(self.centralwidget)
        self.frame.setGeometry(QtCore.QRect(30, 20, 320, 500))
        self.frame.setObjectName("frame")

        # --- header: name, version, actions, credit ---
        self.headerRow = QtWidgets.QWidget(self.frame)
        self.headerRow.setGeometry(QtCore.QRect(20, 12, 280, 40))
        header = QtWidgets.QHBoxLayout(self.headerRow)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        self.title = _plain_label(self.headerRow, "title")
        # tall enough for the descender of "y" in any OS font
        self.title.setFixedHeight(36)
        header.addWidget(self.title)
        self.version = _plain_label(self.headerRow, "version")
        self.version.setFixedHeight(16)
        header.addWidget(self.version, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        header.addStretch(1)
        self.SettingsBtn = QtWidgets.QPushButton(self.headerRow)
        self.SettingsBtn.setObjectName("SettingsBtn")
        self.SettingsBtn.setFixedSize(28, 28)
        self.SettingsBtn.setIcon(_line_icon("gear"))
        self.SettingsBtn.setIconSize(QtCore.QSize(16, 16))
        self.SettingsBtn.setToolTip("Settings")
        header.addWidget(self.SettingsBtn)
        self.Closed = QtWidgets.QPushButton(self.headerRow)
        self.Closed.setObjectName("Closed")
        self.Closed.setFixedSize(28, 28)
        self.Closed.setIcon(_line_icon("close"))
        self.Closed.setIconSize(QtCore.QSize(16, 16))
        self.Closed.setToolTip("Close")
        header.addWidget(self.Closed)

        self.author = _plain_label(self.frame, "author")
        self.author.setGeometry(QtCore.QRect(20, 52, 280, 20))

        # --- link + main action ---
        self.PlaylistLink = QtWidgets.QLineEdit(self.frame)
        self.PlaylistLink.setGeometry(QtCore.QRect(20, 92, 280, 44))
        self.PlaylistLink.setMouseTracking(False)
        self.PlaylistLink.setAcceptDrops(False)
        self.PlaylistLink.setWhatsThis("Enter Spotify playlist Link")
        self.PlaylistLink.setClearButtonEnabled(True)
        self.PlaylistLink.setObjectName("PlaylistLink")

        self.DownloadBtn = QtWidgets.QPushButton(self.frame)
        self.DownloadBtn.setGeometry(QtCore.QRect(20, 148, 280, 44))
        self.DownloadBtn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.DownloadBtn.setObjectName("DownloadBtn")

        # --- activity card: a hint while idle, live progress while downloading ---
        self.activityCard = QtWidgets.QFrame(self.frame)
        self.activityCard.setGeometry(QtCore.QRect(20, 208, 280, 196))
        self.activityCard.setObjectName("activityCard")
        card = QtWidgets.QVBoxLayout(self.activityCard)
        card.setContentsMargins(16, 12, 16, 12)
        card.setSpacing(8)

        self.idleBox = QtWidgets.QWidget(self.activityCard)
        idle = QtWidgets.QVBoxLayout(self.idleBox)
        idle.setContentsMargins(0, 0, 0, 0)
        idle.setSpacing(4)
        idle.addStretch(1)
        self.idleIcon = _plain_label(self.idleBox, "idleIcon")
        self.idleTitle = _plain_label(self.idleBox, "idleTitle")
        self.idleHint = _plain_label(self.idleBox, "idleHint", wrap=True)
        for label in (self.idleIcon, self.idleTitle, self.idleHint):
            label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            idle.addWidget(label)
        idle.addStretch(1)
        card.addWidget(self.idleBox, 1)

        self.activeBox = QtWidgets.QWidget(self.activityCard)
        active = QtWidgets.QVBoxLayout(self.activeBox)
        active.setContentsMargins(0, 0, 0, 0)
        active.setSpacing(2)
        self.playlistCaption = _plain_label(self.activeBox, "playlistCaption")
        active.addWidget(self.playlistCaption)
        self.AlbumName = _plain_label(self.activeBox, "AlbumName")
        active.addWidget(self.AlbumName)
        active.addSpacing(8)
        self.PlaylistMsg_2 = _plain_label(self.activeBox, "PlaylistMsg_2")
        active.addWidget(self.PlaylistMsg_2)
        self.MainSongName = _plain_label(self.activeBox, "MainSongName", wrap=True)
        self.MainSongName.setFixedHeight(36)
        self.MainSongName.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.AlignmentFlag.AlignLeft
        )
        active.addWidget(self.MainSongName)
        active.addStretch(1)
        self.activeBox.hide()
        card.addWidget(self.activeBox, 1)

        self.SongDownloadprogress = QtWidgets.QProgressBar(self.activityCard)
        self.SongDownloadprogress.setFixedHeight(4)
        self.SongDownloadprogress.setProperty("value", 0)
        self.SongDownloadprogress.setTextVisible(False)
        self.SongDownloadprogress.setObjectName("SongDownloadprogress")
        self.SongDownloadprogress.hide()
        card.addWidget(self.SongDownloadprogress)

        self.CounterLabel = _plain_label(self.activityCard, "CounterLabel")
        self.CounterLabel.hide()
        card.addWidget(self.CounterLabel)

        # status text on the left, the end-of-run report button on the right
        self.statusRow = QtWidgets.QWidget(self.activityCard)
        status_row = QtWidgets.QHBoxLayout(self.statusRow)
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(8)
        self.statusMsg = StatusLabel(self.statusRow)
        self.statusMsg.setObjectName("statusMsg")
        self.statusMsg.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.statusMsg.setWordWrap(True)
        # a long unbroken url must clip, not widen the card
        self.statusMsg.setMinimumWidth(1)
        self.statusMsg.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed
        )
        self.statusMsg.setFixedHeight(32)
        self.statusMsg.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.AlignmentFlag.AlignHCenter
        )
        status_row.addWidget(self.statusMsg, 1)
        self.ReportBtn = QtWidgets.QPushButton(self.statusRow)
        self.ReportBtn.setObjectName("ReportBtn")
        self.ReportBtn.setFixedHeight(20)
        self.ReportBtn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.ReportBtn.hide()
        status_row.addWidget(self.ReportBtn, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        card.addWidget(self.statusRow)

        # --- options ---
        self.optionsRow = QtWidgets.QWidget(self.frame)
        self.optionsRow.setGeometry(QtCore.QRect(20, 416, 280, 24))
        options = QtWidgets.QHBoxLayout(self.optionsRow)
        options.setContentsMargins(0, 0, 0, 0)
        options.setSpacing(8)
        self.showPreviewCheck = ToggleSwitch(self.optionsRow)
        self.showPreviewCheck.setObjectName("showPreviewCheck")
        options.addWidget(self.showPreviewCheck)
        options.addStretch(1)
        self.AddMetaDataCheck = ToggleSwitch(self.optionsRow)
        self.AddMetaDataCheck.setObjectName("AddMetaDataCheck")
        options.addWidget(self.AddMetaDataCheck)

        # --- footer ---
        self.Select_Home = QtWidgets.QPushButton(self.frame)
        self.Select_Home.setGeometry(QtCore.QRect(70, 452, 180, 32))
        self.Select_Home.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.Select_Home.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.Select_Home.setObjectName("Select_Home")

        # --- preview drawer, slides out from behind the main card. Children keep
        # fixed geometry so its width animation clips them instead of reflowing.
        self.SONGINFORMATION = QtWidgets.QFrame(self.centralwidget)
        self.SONGINFORMATION.setGeometry(QtCore.QRect(345, 30, 0, 480))
        self.SONGINFORMATION.setObjectName("SONGINFORMATION")
        # full-width content host: the drawer's width animation clips it
        # instead of squeezing the layout. The left 5px sit under the main card.
        self.drawerContent = QtWidgets.QWidget(self.SONGINFORMATION)
        self.drawerContent.setGeometry(QtCore.QRect(5, 0, 345, 480))
        drawer = QtWidgets.QVBoxLayout(self.drawerContent)
        drawer.setContentsMargins(28, 24, 29, 24)
        drawer.setSpacing(0)
        # text sits on the cover's left edge, so a two-line title only pushes
        # the lines below it and the cover never jumps between songs
        self.label_3 = _plain_label(self.drawerContent, "label_3")
        drawer.addWidget(self.label_3)
        drawer.addSpacing(16)
        self.CoverImg = CoverArt(self.drawerContent)
        self.CoverImg.setFixedSize(288, 288)
        self.CoverImg.setObjectName("CoverImg")
        drawer.addWidget(self.CoverImg)
        drawer.addSpacing(20)
        self.SongName = _plain_label(self.drawerContent, "SongName", wrap=True)
        drawer.addWidget(self.SongName)
        drawer.addSpacing(4)
        self.ArtistNameText = _plain_label(self.drawerContent, "ArtistNameText", wrap=True)
        drawer.addWidget(self.ArtistNameText)
        drawer.addSpacing(4)
        self.YearText = StatusLabel(self.drawerContent)
        self.YearText.setObjectName("YearText")
        self.YearText.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        drawer.addWidget(self.YearText)
        self.YearText.hide()  # shown once a release date arrives
        drawer.addStretch(1)

        self.SONGINFORMATION.raise_()
        self.frame.raise_()
        MainWindow.setCentralWidget(self.centralwidget)

        self.retranslateUi(MainWindow)
        QtCore.QMetaObject.connectSlotsByName(MainWindow)

    def retranslateUi(self, MainWindow):
        _translate = QtCore.QCoreApplication.translate
        MainWindow.setWindowTitle(_translate("MainWindow", "Sunnify"))
        self.title.setText(_translate("MainWindow", "Sunnify"))
        self.DownloadBtn.setText(_translate("MainWindow", "Download"))
        self.PlaylistLink.setPlaceholderText(_translate("MainWindow", "Paste Spotify URL"))
        self.author.setText(
            _translate("MainWindow", "Spotify Downloader · Created by Sunny Patel")
        )
        self.idleIcon.setPixmap(_record_pixmap(32))
        self.idleTitle.setText(_translate("MainWindow", "Paste a link to get started"))
        self.idleHint.setText(
            _translate("MainWindow", "Playlists, albums and single tracks")
        )
        self.playlistCaption.setText(_translate("MainWindow", "Playlist"))
        self.PlaylistMsg_2.setText(_translate("MainWindow", "Now downloading"))
        # empty while idle: the card already says what to do; this line only
        # carries real news (errors, "Settings saved", a finished run)
        self.statusMsg.setText("")
        self.Select_Home.setText(_translate("MainWindow", "Follow me on LinkedIn  ↗"))
        self.version.setText(_translate("MainWindow", "V2.6.0"))
        self.showPreviewCheck.setText(_translate("MainWindow", "Show preview"))
        self.AddMetaDataCheck.setText(_translate("MainWindow", "Add meta tags"))
        self.label_3.setText(_translate("MainWindow", "Song information"))
        self.SongName.setText(_translate("MainWindow", "No song yet"))
        self.ArtistNameText.setText(
            _translate("MainWindow", "Details show up here during a download")
        )
