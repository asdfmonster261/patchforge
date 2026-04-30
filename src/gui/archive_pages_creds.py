"""Credentials sub-page — opens a modal dialog editing
archive_credentials.json (NEVER the .xarchive).

The sidebar entry is a sub-page rather than a button so the navigation
list stays uniform.  The page itself is mostly a "click here to open"
landing surface plus a small status summary.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
    QVBoxLayout, QWidget,
)

from ..core.archive import credentials as creds_mod
from .archive_pages import ArchivePageBase


class CredentialsPage(ArchivePageBase):
    def __init__(self, panel):
        super().__init__(panel)
        layout = QVBoxLayout(self)

        grp = QGroupBox("Credentials")
        v = QVBoxLayout(grp)
        v.setContentsMargins(12, 14, 12, 14)
        v.setSpacing(8)
        v.addWidget(QLabel(
            "Stored at ~/.config/patchforge/archive_credentials.json — never\n"
            "written to the .xarchive project file (which is meant to be\n"
            "shareable / version-controllable)."
        ))
        self.summary = QLabel("(loading…)")
        self.summary.setObjectName("dim")
        v.addWidget(self.summary)
        row = QHBoxLayout()
        self.btn_edit = QPushButton("Edit credentials…")
        self.btn_edit.setObjectName("accent")
        self.btn_edit.clicked.connect(self._on_edit)
        row.addWidget(self.btn_edit)
        row.addStretch(1)
        v.addLayout(row)
        layout.addWidget(grp)
        layout.addStretch(1)

    # ---------------------------------------------------------- protocol
    def refresh(self):
        try:
            c = creds_mod.load()
        except Exception:
            self.summary.setText("(unable to read credentials file)")
            return
        bits = []
        bits.append("Steam: " + ("✔" if c.has_login_tokens() else "—"))
        bits.append("Web API: " + ("✔" if c.web_api_key else "—"))
        bits.append("MultiUp: " + ("✔" if c.multiup.is_set() else "—"))
        bits.append("PrivateBin: " + ("✔" if c.privatebin.is_set() else "—"))
        bits.append("Telegram: " + ("✔" if c.telegram.is_set() else "—"))
        bits.append("Discord: " + ("✔" if c.discord.is_set() else "—"))
        self.summary.setText("   ".join(bits))

    def flush(self): pass

    # ---------------------------------------------------------- handler
    def _on_edit(self):
        dlg = CredentialsDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self.refresh()


class CredentialsDialog(QDialog):
    """Modal editor for archive_credentials.json.

    Steam refresh tokens go through `patchforge archive login` (an
    interactive 2FA flow) so this dialog only displays the existing
    token state — re-auth opens the CLI in the user's terminal.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Credentials")
        self.setModal(True)
        self.resize(680, 560)
        self._creds = creds_mod.load()
        self._build_ui()
        self._populate()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.addWidget(QLabel(
            "Saved to ~/.config/patchforge/archive_credentials.json — never written\n"
            "to project files."
        ))

        # ── Steam ───────────────────────────────────────────────────
        steam_grp = QGroupBox("Steam (depot downloads)")
        sf = QFormLayout(steam_grp)
        self.steam_username = QLineEdit()
        self.steam_username.setPlaceholderText("Steam login username")
        self.steam_id_label = QLabel()
        self.steam_id_label.setObjectName("dim")
        self.token_label = QLabel()
        self.token_label.setObjectName("dim")
        sf.addRow("Username:", self.steam_username)
        sf.addRow("Steam64 ID:", self.steam_id_label)
        sf.addRow("Refresh token:", self.token_label)
        sf.addRow(QLabel(
            "To (re-)authenticate, run `patchforge archive login` in a terminal.\n"
            "PatchForge never sees your password — only the resulting refresh token."
        ))
        root.addWidget(steam_grp)

        # ── Web API ─────────────────────────────────────────────────
        api_grp = QGroupBox("Steam Web API")
        af = QFormLayout(api_grp)
        self.web_api_key = QLineEdit()
        self.web_api_key.setEchoMode(QLineEdit.Password)
        self.web_api_key.setPlaceholderText("(optional — used for achievement schema)")
        af.addRow("API key:", self.web_api_key)
        root.addWidget(api_grp)

        # ── MultiUp + PrivateBin ────────────────────────────────────
        up_grp = QGroupBox("MultiUp + PrivateBin (uploads)")
        uf = QFormLayout(up_grp)
        self.mu_username = QLineEdit()
        self.mu_password = QLineEdit()
        self.mu_password.setEchoMode(QLineEdit.Password)
        self.pb_url = QLineEdit()
        self.pb_url.setPlaceholderText("https://privatebin.net")
        self.pb_pass = QLineEdit()
        self.pb_pass.setEchoMode(QLineEdit.Password)
        self.pb_pass.setPlaceholderText("(optional)")
        uf.addRow("MultiUp user:",     self.mu_username)
        uf.addRow("MultiUp password:", self.mu_password)
        uf.addRow("PrivateBin URL:",   self.pb_url)
        uf.addRow("PrivateBin pass:",  self.pb_pass)
        root.addWidget(up_grp)

        # ── Notify ─────────────────────────────────────────────────
        notify_grp = QGroupBox("Notify channels")
        nf = QFormLayout(notify_grp)
        self.tg_token = QLineEdit()
        self.tg_token.setEchoMode(QLineEdit.Password)
        self.tg_chats = QLineEdit()
        self.tg_chats.setPlaceholderText("chat_id1, chat_id2, …")
        self.dc_webhook = QLineEdit()
        self.dc_webhook.setEchoMode(QLineEdit.Password)
        self.dc_mentions = QLineEdit()
        self.dc_mentions.setPlaceholderText("role_id1, role_id2, …")
        nf.addRow("Telegram bot token:", self.tg_token)
        nf.addRow("Telegram chat IDs:",  self.tg_chats)
        nf.addRow("Discord webhook URL:", self.dc_webhook)
        nf.addRow("Discord mention roles:", self.dc_mentions)
        root.addWidget(notify_grp)

        # ── footer ─────────────────────────────────────────────────
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._on_save)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

    def _populate(self):
        c = self._creds
        self.steam_username.setText(c.username)
        self.steam_id_label.setText(str(c.steam_id) if c.steam_id else "(none — run `archive login`)")
        if c.client_refresh_token:
            tok = c.client_refresh_token
            self.token_label.setText(f"set ({tok[:6]}…{tok[-4:]})")
        else:
            self.token_label.setText("(none — run `archive login`)")
        self.web_api_key.setText(c.web_api_key)

        self.mu_username.setText(c.multiup.username)
        self.mu_password.setText(c.multiup.password)
        self.pb_url.setText(c.privatebin.url)
        self.pb_pass.setText(c.privatebin.password)
        self.tg_token.setText(c.telegram.token)
        self.tg_chats.setText(", ".join(c.telegram.chat_ids))
        self.dc_webhook.setText(c.discord.webhook_url)
        self.dc_mentions.setText(", ".join(c.discord.mention_role_ids))

    def _on_save(self):
        c = self._creds
        c.username = self.steam_username.text().strip()
        c.web_api_key = self.web_api_key.text().strip()
        c.multiup.username = self.mu_username.text().strip()
        c.multiup.password = self.mu_password.text()
        c.privatebin.url = self.pb_url.text().strip()
        c.privatebin.password = self.pb_pass.text()
        c.telegram.token = self.tg_token.text().strip()
        c.telegram.chat_ids = [s.strip() for s in self.tg_chats.text().split(",") if s.strip()]
        c.discord.webhook_url = self.dc_webhook.text().strip()
        c.discord.mention_role_ids = [s.strip() for s in self.dc_mentions.text().split(",") if s.strip()]
        try:
            creds_mod.save(c)
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self.accept()


__all__ = ["CredentialsPage", "CredentialsDialog"]
