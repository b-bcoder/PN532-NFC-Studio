import io
import queue
import threading
import time
import tkinter as tk
from contextlib import redirect_stdout
from tkinter import messagebox
from urllib.parse import urlsplit

import serial

import Deleter
import Lezer
import Schrijver
from pn532_common import (
    CLASSIC_NDEF_CAPACITY,
    find_tag,
    get_mad_ndef_sectors,
    make_ndef_tlv,
    open_reader,
    read_classic_ndef,
)


BACKGROUND = "#0B1220"
SURFACE = "#111D30"
SURFACE_LIGHT = "#17263B"
BORDER = "#26364D"
TEXT = "#F2F6FC"
MUTED = "#A5B3C7"
CYAN = "#32D5DF"
GREEN = "#54D69A"
YELLOW = "#F1BD55"
RED = "#F17A83"


def prepare_write_payload(content, kind):
    content = content.strip() if kind == "url" else content
    if not content:
        raise ValueError("Vul eerst tekst of een URL in.")

    if kind == "url":
        try:
            parsed = urlsplit(content)
            valid_url = (
                parsed.scheme in ("http", "https")
                and parsed.hostname is not None
            )
        except ValueError:
            valid_url = False
        if not valid_url:
            raise ValueError(
                "Voer een geldige URL in die begint met http:// of https://."
            )

    ndef_tlv = make_ndef_tlv(content, kind)
    if len(ndef_tlv) > CLASSIC_NDEF_CAPACITY:
        raise ValueError(
            f"Deze tag heeft {CLASSIC_NDEF_CAPACITY} bytes NDEF-ruimte; "
            f"de invoer vraagt {len(ndef_tlv)} bytes."
        )
    return content, ndef_tlv


def _tag_details(tag):
    return {
        "uid": tag["uid"].hex(":").upper(),
        "type": Lezer.describe_tag(tag["sak"]),
        "sak": tag["sak"],
    }


def _wait_for_tag(port, cancel_event):
    while not cancel_event.is_set():
        tag = find_tag(port)
        if tag is not None:
            return tag
        time.sleep(0.2)
    return None


def _confirm_same_tag(port, original_tag):
    tag = find_tag(port)
    if tag is None:
        raise RuntimeError(
            "De tag is niet meer gedetecteerd. Leg dezelfde tag terug en start opnieuw."
        )
    if tag["uid"] != original_tag["uid"]:
        raise RuntimeError(
            "Er ligt nu een andere tag op de module. Start de actie opnieuw."
        )
    return tag


class Pn532DesktopApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PN532 NFC Studio")
        self.root.geometry("900x760")
        self.root.minsize(760, 650)
        self.root.configure(bg=BACKGROUND)

        self.events = queue.Queue()
        self.cancel_event = None
        self.confirm_event = None
        self.busy = False
        self.current_tab = "read"
        self.write_kind = tk.StringVar(value="url")
        self.confirm_button = None
        self.cancel_button = None
        self.write_type_buttons = []

        self._build_shell()
        self._show_tab("read")
        self.root.after(100, self._process_events)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _build_shell(self):
        self.shell = tk.Frame(self.root, bg=BACKGROUND)
        self.shell.pack(fill="both", expand=True, padx=34, pady=28)

        header = tk.Frame(self.shell, bg=BACKGROUND)
        header.pack(fill="x", pady=(0, 22))

        logo = tk.Frame(header, bg=CYAN, width=48, height=48)
        logo.pack(side="left")
        logo.pack_propagate(False)
        tk.Label(
            logo,
            text="N",
            bg=CYAN,
            fg=BACKGROUND,
            font=("Segoe UI", 22, "bold"),
        ).pack(expand=True)

        title_block = tk.Frame(header, bg=BACKGROUND)
        title_block.pack(side="left", padx=(14, 0))
        tk.Label(
            title_block,
            text="PN532 NFC Studio",
            bg=BACKGROUND,
            fg=TEXT,
            font=("Segoe UI", 20, "bold"),
        ).pack(anchor="w")
        tk.Label(
            title_block,
            text="Lees, schrijf en wis NFC-tags",
            bg=BACKGROUND,
            fg=MUTED,
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(2, 0))

        self.connection_label = tk.Label(
            header,
            text="●  Niet verbonden",
            bg=SURFACE_LIGHT,
            fg=MUTED,
            font=("Segoe UI", 9, "bold"),
            padx=12,
            pady=8,
        )
        self.connection_label.pack(side="right", anchor="center")

        self.tabs_frame = tk.Frame(self.shell, bg=SURFACE_LIGHT)
        self.tabs_frame.pack(fill="x", pady=(0, 18))
        self.tab_buttons = {}
        for key, label in (
            ("read", "Read Tag"),
            ("write", "Write Tag"),
            ("delete", "Delete Tag"),
        ):
            button = tk.Button(
                self.tabs_frame,
                text=label,
                command=lambda selected=key: self._show_tab(selected),
                relief="flat",
                bd=0,
                padx=18,
                pady=12,
                cursor="hand2",
                font=("Segoe UI", 10, "bold"),
                bg=SURFACE_LIGHT,
                fg=MUTED,
                activebackground=SURFACE,
                activeforeground=TEXT,
            )
            button.pack(side="left", expand=True, fill="x", padx=4, pady=4)
            self.tab_buttons[key] = button

        self.page = tk.Frame(
            self.shell,
            bg=SURFACE,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        self.page.pack(fill="both", expand=True)

        self.footer_message = tk.Label(
            self.shell,
            text="Sluit de PN532 via USB aan. De verbinding wordt gecontroleerd zodra je een actie start.",
            bg=BACKGROUND,
            fg=MUTED,
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
            wraplength=820,
        )
        self.footer_message.pack(fill="x", pady=(14, 0))

    def _label(self, parent, text, *, size=10, color=TEXT, bold=False, **kwargs):
        return tk.Label(
            parent,
            text=text,
            bg=kwargs.pop("bg", SURFACE),
            fg=color,
            font=("Segoe UI", size, "bold" if bold else "normal"),
            **kwargs,
        )

    def _card_title(self, title, subtitle):
        self._label(self.page, title, size=17, bold=True).pack(
            anchor="w", padx=26, pady=(25, 3)
        )
        self._label(
            self.page,
            subtitle,
            size=10,
            color=MUTED,
            wraplength=790,
            justify="left",
        ).pack(anchor="w", padx=26, pady=(0, 18))

    def _show_tab(self, tab):
        if self.busy:
            return
        self.current_tab = tab
        self.confirm_button = None
        self.cancel_button = None
        self.write_type_buttons = []
        for key, button in self.tab_buttons.items():
            selected = key == tab
            button.configure(
                bg="#1A3A4D" if selected else SURFACE_LIGHT,
                fg=CYAN if selected else MUTED,
            )
        for child in self.page.winfo_children():
            child.destroy()

        if tab == "read":
            self._build_read_page()
        elif tab == "write":
            self._build_write_page()
        else:
            self._build_delete_page()

    def _build_read_page(self):
        self._card_title(
            "NFC-tag uitlezen",
            "Leg een tag op de antenne van de PN532. De gevonden tekst of URL verschijnt hieronder.",
        )
        self.action_button = self._action_button(
            self.page, "Read Tag", self._start_read, CYAN
        )
        self.action_button.pack(fill="x", padx=26, pady=(0, 18))

        self._label(self.page, "LEESRESULTAAT", size=9, color=MUTED, bold=True).pack(
            anchor="w", padx=26, pady=(0, 7)
        )
        result_frame = tk.Frame(self.page, bg=SURFACE_LIGHT)
        result_frame.pack(fill="both", expand=True, padx=26, pady=(0, 25))
        self.result_text = tk.Text(
            result_frame,
            height=12,
            wrap="word",
            bg=SURFACE_LIGHT,
            fg=TEXT,
            insertbackground=TEXT,
            selectbackground="#28566A",
            relief="flat",
            bd=0,
            padx=14,
            pady=12,
            font=("Segoe UI", 10),
            state="disabled",
        )
        scrollbar = tk.Scrollbar(result_frame, command=self.result_text.yview)
        self.result_text.configure(yscrollcommand=scrollbar.set)
        self.result_text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self._set_text(
            self.result_text,
            "Nog geen tag gelezen.\n\nKlik op Read Tag en leg een NFC-tag op de module.",
        )

    def _build_write_page(self):
        self._card_title(
            "NFC-tag beschrijven",
            "Kies URL of tekst en vul de inhoud in. Je bevestigt het schrijven nadat de tag is gedetecteerd.",
        )
        choice_row = tk.Frame(self.page, bg=SURFACE)
        choice_row.pack(fill="x", padx=26, pady=(0, 16))
        self.write_type_buttons = [
            self._choice_button(choice_row, "url", "Website URL"),
            self._choice_button(choice_row, "text", "Vrije tekst"),
        ]
        self.write_type_buttons[0].pack(
            side="left", expand=True, fill="x", padx=(0, 5)
        )
        self.write_type_buttons[1].pack(
            side="left", expand=True, fill="x", padx=(5, 0)
        )

        self._label(self.page, "INHOUD", size=9, color=MUTED, bold=True).pack(
            anchor="w", padx=26, pady=(0, 7)
        )
        entry_frame = tk.Frame(
            self.page,
            bg=SURFACE_LIGHT,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        entry_frame.pack(fill="x", padx=26)
        self.write_entry = tk.Text(
            entry_frame,
            height=6,
            wrap="word",
            bg=SURFACE_LIGHT,
            fg=TEXT,
            insertbackground=CYAN,
            selectbackground="#28566A",
            relief="flat",
            bd=0,
            padx=12,
            pady=11,
            font=("Segoe UI", 11),
        )
        self.write_entry.pack(fill="both", expand=True)
        self.write_entry.bind("<<Modified>>", self._update_byte_count)

        self.byte_count = tk.StringVar(value=f"0 / {CLASSIC_NDEF_CAPACITY} bytes")
        self._label(
            self.page,
            "Alleen MIFARE Classic-tags die met Delete Tag NDEF-klaar zijn gemaakt.",
            size=9,
            color=MUTED,
            wraplength=780,
            justify="left",
        ).pack(anchor="w", padx=26, pady=(9, 2))
        tk.Label(
            self.page,
            textvariable=self.byte_count,
            bg=SURFACE,
            fg=MUTED,
            font=("Segoe UI", 9),
        ).pack(anchor="e", padx=26, pady=(0, 17))

        self.action_button = self._action_button(
            self.page, "Write Tag", self._start_write, CYAN
        )
        self.action_button.pack(fill="x", padx=26, pady=(0, 8))
        self.confirm_button = self._action_button(
            self.page, "Bevestig schrijven", self._confirm, GREEN
        )
        self.confirm_button.pack_forget()
        self.cancel_button = self._cancel_button(self.page)
        self.cancel_button.pack_forget()
        self.details_label = self._label(
            self.page, "", size=9, color=GREEN, wraplength=780, justify="left"
        )
        self.details_label.pack(anchor="w", padx=26, pady=(0, 22))

    def _choice_button(self, parent, kind, label):
        selected = self.write_kind.get() == kind
        return tk.Radiobutton(
            parent,
            text=label,
            variable=self.write_kind,
            value=kind,
            command=self._update_write_choices,
            indicatoron=False,
            relief="flat",
            bd=0,
            padx=12,
            pady=11,
            cursor="hand2",
            bg="#1A3A4D" if selected else SURFACE_LIGHT,
            fg=CYAN if selected else MUTED,
            selectcolor="#1A3A4D",
            activebackground=SURFACE_LIGHT,
            activeforeground=TEXT,
            font=("Segoe UI", 10, "bold"),
        )

    def _cancel_button(self, parent):
        return tk.Button(
            parent,
            text="Annuleren",
            command=self._cancel,
            relief="flat",
            bd=0,
            padx=16,
            pady=10,
            cursor="hand2",
            bg=SURFACE_LIGHT,
            fg=MUTED,
            activebackground=BORDER,
            activeforeground=TEXT,
            font=("Segoe UI", 9, "bold"),
        )

    def _update_write_choices(self):
        for child in self.page.winfo_children():
            if isinstance(child, tk.Frame) and child.winfo_children():
                for item in child.winfo_children():
                    if isinstance(item, tk.Radiobutton):
                        selected = item.cget("value") == self.write_kind.get()
                        item.configure(
                            bg="#1A3A4D" if selected else SURFACE_LIGHT,
                            fg=CYAN if selected else MUTED,
                        )
        self._update_byte_count()

    def _update_byte_count(self, _event=None):
        if not hasattr(self, "write_entry") or not self.write_entry.winfo_exists():
            return
        value = self.write_entry.get("1.0", "end-1c")
        if self.write_kind.get() == "url":
            value = value.strip()
        if not value:
            self.byte_count.set(f"0 / {CLASSIC_NDEF_CAPACITY} bytes")
            self.write_entry.edit_modified(False)
            return
        try:
            size = len(make_ndef_tlv(value, self.write_kind.get()))
            self.byte_count.set(f"{size} / {CLASSIC_NDEF_CAPACITY} bytes")
        except ValueError:
            self.byte_count.set(f"Te lang voor {CLASSIC_NDEF_CAPACITY} bytes")
        self.write_entry.edit_modified(False)

    def _build_delete_page(self):
        self._card_title(
            "Tag wissen / formatteren",
            "De app detecteert eerst je tag. Controleer de getoonde UID en druk daarna op Confirm om door te gaan.",
        )
        info = tk.Frame(
            self.page,
            bg=SURFACE_LIGHT,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        info.pack(fill="x", padx=26, pady=(0, 18))
        tk.Label(
            info,
            text="⚠",
            bg=SURFACE_LIGHT,
            fg=YELLOW,
            font=("Segoe UI", 22, "bold"),
        ).pack(side="left", padx=(16, 12), pady=15)
        tk.Label(
            info,
            text=(
                "Deze actie wist de NDEF-inhoud of maakt een lege MIFARE Classic-tag "
                "NDEF-klaar. Alleen een lege fabrieks-tag of een al geformatteerde "
                "tag wordt geaccepteerd."
            ),
            bg=SURFACE_LIGHT,
            fg=MUTED,
            font=("Segoe UI", 10),
            wraplength=690,
            justify="left",
        ).pack(side="left", fill="x", expand=True, padx=(0, 16), pady=15)

        self.detected_panel = tk.Frame(
            self.page,
            bg="#142B2D",
            highlightbackground="#286458",
            highlightthickness=1,
        )
        self.details_label = tk.Label(
            self.detected_panel,
            text="",
            bg="#142B2D",
            fg=GREEN,
            font=("Segoe UI", 10),
            justify="left",
            anchor="w",
            wraplength=760,
        )
        self.details_label.pack(fill="x", padx=15, pady=13)
        self.detected_panel.pack(fill="x", padx=26, pady=(0, 12))
        self.detected_panel.pack_forget()

        self.action_button = self._action_button(
            self.page, "Delete Tag", self._start_delete, YELLOW
        )
        self.action_button.pack(fill="x", padx=26, pady=(0, 8))
        self.confirm_button = self._action_button(
            self.page, "Confirm · wis tag", self._confirm, RED
        )
        self.confirm_button.pack_forget()
        self.cancel_button = self._cancel_button(self.page)
        self.cancel_button.pack_forget()

    def _action_button(self, parent, text, command, color):
        return tk.Button(
            parent,
            text=text,
            command=command,
            relief="flat",
            bd=0,
            padx=16,
            pady=13,
            cursor="hand2",
            bg=color,
            fg=BACKGROUND,
            activebackground=color,
            activeforeground=BACKGROUND,
            disabledforeground="#718097",
            font=("Segoe UI", 10, "bold"),
        )

    def _set_text(self, widget, value):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def _begin(self, operation, *args):
        if self.busy:
            return
        self.busy = True
        self.cancel_event = threading.Event()
        self.confirm_event = threading.Event()
        for button in self.tab_buttons.values():
            button.configure(state="disabled")
        if self.current_tab == "write":
            self.write_entry.configure(state="disabled")
            for button in self.write_type_buttons:
                button.configure(state="disabled")
        self.action_button.configure(
            text="Scannen annuleren...",
            command=self._cancel,
            state="normal",
        )
        self.connection_label.configure(text="●  Bezig met verbinden", fg=YELLOW)
        self.footer_message.configure(
            text="Houd de tag stil op de antenne van de PN532."
        )
        threading.Thread(
            target=self._run_operation,
            args=(operation, args),
            daemon=True,
        ).start()

    def _start_read(self):
        self._set_text(self.result_text, "Wachten op een NFC-tag...")
        self._begin("read")

    def _start_write(self):
        content = self.write_entry.get("1.0", "end-1c")
        try:
            content, ndef_tlv = prepare_write_payload(
                content, self.write_kind.get()
            )
        except ValueError as error:
            messagebox.showerror("Ongeldige invoer", str(error), parent=self.root)
            return
        self.details_label.configure(text="")
        self._begin("write", content, ndef_tlv)

    def _start_delete(self):
        self.details_label.configure(text="")
        self._begin("delete")

    def _cancel(self):
        if self.cancel_event is not None:
            self.cancel_event.set()
        self.action_button.configure(text="Annuleren...", state="disabled")
        if (
            self.confirm_button is not None
            and self.confirm_button.winfo_exists()
            and self.confirm_button.winfo_manager()
        ):
            self.confirm_button.configure(state="disabled")
        if (
            self.cancel_button is not None
            and self.cancel_button.winfo_exists()
            and self.cancel_button.winfo_manager()
        ):
            self.cancel_button.configure(state="disabled")
        self.footer_message.configure(text="Actie wordt geannuleerd...")

    def _confirm(self):
        if self.confirm_event is not None:
            self.confirm_event.set()
        self.action_button.configure(state="disabled")
        self.confirm_button.configure(text="Bezig...", state="disabled")
        self.cancel_button.configure(state="disabled")
        self.connection_label.configure(text="●  Tag wordt bijgewerkt", fg=YELLOW)
        self.footer_message.configure(text="Laat de tag op de PN532 liggen.")

    def _run_operation(self, operation, args):
        try:
            with redirect_stdout(io.StringIO()):
                if operation == "read":
                    self._read_tag()
                elif operation == "write":
                    self._write_tag(*args)
                else:
                    self._delete_tag()
        except (serial.SerialException, TimeoutError, RuntimeError, ValueError) as error:
            self.events.put(("error", str(error)))

    def _read_tag(self):
        self.events.put(("status", "Verbinden met de PN532..."))
        with open_reader() as port:
            self.events.put(("status", "Leg een NFC-tag op de module..."))
            tag = _wait_for_tag(port, self.cancel_event)
            if tag is None:
                self.events.put(("cancelled", "Uitlezen geannuleerd."))
                return

            details = _tag_details(tag)
            output = io.StringIO()
            with redirect_stdout(output):
                Lezer.read_tag_content(port, tag)
            lines = [
                f"Type: {details['type']}",
                f"UID:  {details['uid']}",
                f"SAK:  0x{details['sak']:02X}",
                f"ATQA: {tag['atqa'].hex(' ').upper()}",
            ]
            if tag["ats"]:
                lines.append(f"ATS:  {tag['ats'].hex(' ').upper()}")
            lines.extend(("", output.getvalue().strip() or "Geen NDEF-inhoud gevonden."))
            self.events.put(("read_result", "\n".join(lines)))

    def _write_tag(self, content, ndef_tlv):
        self.events.put(("status", "Verbinden met de PN532..."))
        with open_reader() as port:
            self.events.put(("status", "Leg de MIFARE Classic-tag op de module..."))
            tag = _wait_for_tag(port, self.cancel_event)
            if tag is None:
                self.events.put(("cancelled", "Schrijven geannuleerd."))
                return
            if tag["sak"] not in (0x08, 0x09, 0x18):
                raise RuntimeError(
                    "Deze schrijver ondersteunt alleen MIFARE Classic-tags."
                )

            sectors = get_mad_ndef_sectors(port, tag["target"], tag["uid"])
            if sectors != [1, 2]:
                raise RuntimeError(
                    "De tag is niet NDEF-klaar gemaakt. Gebruik eerst Delete Tag."
                )
            if read_classic_ndef(port, tag["target"], tag["uid"]) is None:
                raise RuntimeError("De NDEF-directory van de tag kon niet worden gelezen.")

            self.events.put(
                (
                    "write_ready",
                    {
                        **_tag_details(tag),
                        "content": content,
                        "size": len(ndef_tlv),
                    },
                )
            )
            if not self._wait_for_confirmation():
                self.events.put(("cancelled", "Schrijven geannuleerd; de tag is niet gewijzigd."))
                return

            tag = _confirm_same_tag(port, tag)
            try:
                Schrijver.write_ndef(
                    port, tag["target"], tag["uid"], ndef_tlv
                )
            except (serial.SerialException, TimeoutError, RuntimeError, ValueError) as error:
                raise RuntimeError(
                    "Schrijven is begonnen maar niet volledig afgerond; "
                    f"de NDEF-inhoud kan gedeeltelijk gewijzigd zijn. Details: {error}"
                ) from error
            self.events.put(
                ("complete", f"Tag {tag['uid'].hex(':').upper()} is geschreven en gecontroleerd.")
            )

    def _delete_tag(self):
        self.events.put(("status", "Verbinden met de PN532..."))
        with open_reader() as port:
            self.events.put(("status", "Leg de MIFARE Classic-tag op de module..."))
            tag = _wait_for_tag(port, self.cancel_event)
            if tag is None:
                self.events.put(("cancelled", "Wissen geannuleerd."))
                return
            already_formatted = self._check_delete_candidate(port, tag)
            details = _tag_details(tag)
            details["already_formatted"] = already_formatted
            self.events.put(("delete_ready", details))
            if not self._wait_for_confirmation():
                self.events.put(("cancelled", "Wissen geannuleerd; de tag is niet gewijzigd."))
                return

            tag = _confirm_same_tag(port, tag)
            already_formatted = self._check_delete_candidate(port, tag)
            try:
                if already_formatted:
                    Deleter.clear_formatted_ndef(port, tag)
                else:
                    Deleter.format_factory_classic(port, tag)
                Deleter.verify_empty_ndef(port, tag)
            except (serial.SerialException, TimeoutError, RuntimeError, ValueError) as error:
                raise RuntimeError(
                    "Formattering is begonnen maar niet volledig afgerond; "
                    f"de tag kan gedeeltelijk gewijzigd zijn. Details: {error}"
                ) from error
            self.events.put(
                ("complete", f"Tag {tag['uid'].hex(':').upper()} is leeggemaakt.")
            )

    def _check_delete_candidate(self, port, tag):
        if tag["sak"] not in (0x08, 0x09, 0x18):
            raise RuntimeError(
                f"Tag met SAK 0x{tag['sak']:02X} is geen ondersteunde MIFARE Classic-tag."
            )
        if len(tag["uid"]) != 4:
            raise RuntimeError(
                "Alleen Classic-tags met een UID van 4 bytes worden ondersteund."
            )

        already_formatted = Deleter.is_ndef_formatted(port, tag)
        if not already_formatted:
            Deleter.verify_factory_blank(port, tag)
        return already_formatted

    def _wait_for_confirmation(self):
        while not self.confirm_event.wait(0.1):
            if self.cancel_event.is_set():
                return False
        return not self.cancel_event.is_set()

    def _process_events(self):
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == "status":
                    self.footer_message.configure(text=value)
                    if value.startswith("Leg "):
                        self.connection_label.configure(
                            text="●  PN532 verbonden", fg=GREEN
                        )
                elif event == "read_result":
                    self._set_text(self.result_text, value)
                    self.connection_label.configure(text="●  Tag gelezen", fg=GREEN)
                    self._finish()
                elif event == "write_ready":
                    self.details_label.configure(
                        text=(
                            f"Tag gedetecteerd · {value['type']} · UID {value['uid']}\n"
                            f"Inhoud: {value['content']}\n"
                            f"Te schrijven: {value['size']} / {CLASSIC_NDEF_CAPACITY} bytes"
                        )
                    )
                    self._show_confirmation("write")
                elif event == "delete_ready":
                    state = (
                        "al NDEF-geformatteerd"
                        if value["already_formatted"]
                        else "lege fabrieks-tag"
                    )
                    self.details_label.configure(
                        text=(
                            f"Tag gedetecteerd · {value['type']}\n"
                            f"UID: {value['uid']}  ·  Status: {state}"
                        )
                    )
                    self.detected_panel.pack(fill="x", padx=26, pady=(0, 12))
                    self._show_confirmation("delete")
                elif event == "complete":
                    self.connection_label.configure(text="●  Voltooid", fg=GREEN)
                    self.footer_message.configure(text=value)
                    self._finish()
                elif event == "cancelled":
                    self.connection_label.configure(text="●  Geannuleerd", fg=MUTED)
                    self.footer_message.configure(text=value)
                    self._finish()
                elif event == "error":
                    self.connection_label.configure(text="●  Fout", fg=RED)
                    self.footer_message.configure(text=value)
                    self._finish()
                    messagebox.showerror("PN532-fout", value, parent=self.root)
        except queue.Empty:
            pass
        self.root.after(100, self._process_events)

    def _show_confirmation(self, operation):
        self.action_button.configure(
            text="Tag gedetecteerd",
            command=lambda: None,
            state="disabled",
        )
        if operation == "write":
            self.confirm_button.configure(
                text="Confirm · schrijf tag",
                state="normal",
                command=self._confirm,
            )
        else:
            self.confirm_button.configure(
                text="Confirm · wis tag",
                state="normal",
                command=self._confirm,
            )
        self.confirm_button.pack(fill="x", padx=26, pady=(0, 8))
        self.cancel_button.pack(fill="x", padx=26, pady=(0, 18))
        self.connection_label.configure(text="●  Tag gedetecteerd", fg=GREEN)
        self.footer_message.configure(
            text="Controleer de taggegevens. Bevestig alleen als dit de juiste tag is."
        )

    def _finish(self):
        self.busy = False
        self.cancel_event = None
        self.confirm_event = None
        for button in self.tab_buttons.values():
            button.configure(state="normal")
        if self.current_tab == "read":
            self.action_button.configure(
                text="Read Tag", command=self._start_read, state="normal"
            )
        elif self.current_tab == "write":
            self.action_button.configure(
                text="Write Tag", command=self._start_write, state="normal"
            )
            self.write_entry.configure(state="normal")
            for button in self.write_type_buttons:
                button.configure(state="normal")
            self.confirm_button.pack_forget()
            self.confirm_button.configure(text="Bevestig schrijven", state="normal")
            self.cancel_button.pack_forget()
            self.cancel_button.configure(state="normal")
        else:
            self.action_button.configure(
                text="Delete Tag", command=self._start_delete, state="normal"
            )
            self.confirm_button.pack_forget()
            self.confirm_button.configure(text="Confirm · wis tag", state="normal")
            self.cancel_button.pack_forget()
            self.cancel_button.configure(state="normal")
            self.detected_panel.pack_forget()

    def _close(self):
        if self.busy:
            if not messagebox.askyesno(
                "Actie annuleren?",
                "Er loopt nog een NFC-actie. Wil je annuleren en de app sluiten?",
                parent=self.root,
            ):
                return
            self._cancel()
        self.root.destroy()


def main():
    root = tk.Tk()
    Pn532DesktopApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
