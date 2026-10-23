import io
import json
import sys
import threading
import time
from contextlib import redirect_stdout
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


output_lock = threading.Lock()
operation_lock = threading.Lock()
cancel_event = threading.Event()
confirm_event = threading.Event()


def emit(event, **payload):
    message = json.dumps({"event": event, **payload}, ensure_ascii=False)
    with output_lock:
        sys.__stdout__.write(message + "\n")
        sys.__stdout__.flush()


def validate_write(content, kind):
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


def tag_details(tag):
    return {
        "uid": tag["uid"].hex(":").upper(),
        "type": Lezer.describe_tag(tag["sak"]),
        "sak": tag["sak"],
    }


def wait_for_tag():
    global active_port
    while not cancel_event.is_set():
        tag = find_tag(active_port)
        if tag is not None:
            return tag
        time.sleep(0.2)
    return None


def confirm_same_tag(original_tag):
    tag = find_tag(active_port)
    if tag is None:
        raise RuntimeError(
            "De tag is niet meer gedetecteerd. Leg dezelfde tag terug en start opnieuw."
        )
    if tag["uid"] != original_tag["uid"]:
        raise RuntimeError(
            "Er ligt nu een andere tag op de module. Start de actie opnieuw."
        )
    return tag


active_port = None


def wait_for_confirmation():
    while not confirm_event.wait(0.1):
        if cancel_event.is_set():
            return False
    return not cancel_event.is_set()


def check_delete_candidate(tag):
    if tag["sak"] not in (0x08, 0x09, 0x18):
        raise RuntimeError(
            f"Tag met SAK 0x{tag['sak']:02X} is geen ondersteunde MIFARE Classic-tag."
        )
    if len(tag["uid"]) != 4:
        raise RuntimeError(
            "Alleen Classic-tags met een UID van 4 bytes worden ondersteund."
        )

    already_formatted = Deleter.is_ndef_formatted(active_port, tag)
    if not already_formatted:
        Deleter.verify_factory_blank(active_port, tag)
    return already_formatted


def run_read():
    global active_port
    emit("status", message="Verbinden met de PN532...")
    with open_reader() as active_port:
        emit("status", message="Leg een NFC-tag op de module...")
        tag = wait_for_tag()
        if tag is None:
            emit("cancelled", message="Uitlezen geannuleerd.")
            return

        details = tag_details(tag)
        output = io.StringIO()
        with redirect_stdout(output):
            urls = Lezer.read_tag_content(active_port, tag)
        lines = [
            f"Type: {details['type']}",
            f"UID:  {details['uid']}",
            f"SAK:  0x{details['sak']:02X}",
            f"ATQA: {tag['atqa'].hex(' ').upper()}",
        ]
        if tag["ats"]:
            lines.append(f"ATS:  {tag['ats'].hex(' ').upper()}")
        lines.extend(("", output.getvalue().strip() or "Geen NDEF-inhoud gevonden."))
        emit("read-result", content="\n".join(lines), urls=urls)


def run_write(content, kind):
    global active_port
    content, ndef_tlv = validate_write(content, kind)
    emit("status", message="Verbinden met de PN532...")
    with open_reader() as active_port:
        emit("status", message="Leg de MIFARE Classic-tag op de module...")
        tag = wait_for_tag()
        if tag is None:
            emit("cancelled", message="Schrijven geannuleerd.")
            return
        if tag["sak"] not in (0x08, 0x09, 0x18):
            raise RuntimeError(
                "Deze schrijver ondersteunt alleen MIFARE Classic-tags."
            )

        sectors = get_mad_ndef_sectors(
            active_port, tag["target"], tag["uid"]
        )
        if sectors != [1, 2]:
            raise RuntimeError(
                "De tag is niet NDEF-klaar gemaakt. Gebruik eerst Delete Tag."
            )
        if read_classic_ndef(active_port, tag["target"], tag["uid"]) is None:
            raise RuntimeError("De NDEF-directory van de tag kon niet worden gelezen.")

        emit(
            "write-ready",
            **tag_details(tag),
            content=content,
            size=len(ndef_tlv),
        )
        if not wait_for_confirmation():
            emit("cancelled", message="Schrijven geannuleerd; de tag is niet gewijzigd.")
            return

        tag = confirm_same_tag(tag)
        try:
            Schrijver.write_ndef(
                active_port, tag["target"], tag["uid"], ndef_tlv
            )
        except (serial.SerialException, TimeoutError, RuntimeError, ValueError) as error:
            raise RuntimeError(
                "Schrijven is begonnen maar niet volledig afgerond; "
                f"de NDEF-inhoud kan gedeeltelijk gewijzigd zijn. Details: {error}"
            ) from error
        emit(
            "complete",
            message=f"Tag {tag['uid'].hex(':').upper()} is geschreven en gecontroleerd.",
        )


def run_delete():
    global active_port
    emit("status", message="Verbinden met de PN532...")
    with open_reader() as active_port:
        emit("status", message="Leg de MIFARE Classic-tag op de module...")
        tag = wait_for_tag()
        if tag is None:
            emit("cancelled", message="Wissen geannuleerd.")
            return

        already_formatted = check_delete_candidate(tag)
        emit(
            "delete-ready",
            **tag_details(tag),
            already_formatted=already_formatted,
        )
        if not wait_for_confirmation():
            emit("cancelled", message="Wissen geannuleerd; de tag is niet gewijzigd.")
            return

        tag = confirm_same_tag(tag)
        already_formatted = check_delete_candidate(tag)
        try:
            if already_formatted:
                Deleter.clear_formatted_ndef(active_port, tag)
            else:
                Deleter.format_factory_classic(active_port, tag)
            Deleter.verify_empty_ndef(active_port, tag)
        except (serial.SerialException, TimeoutError, RuntimeError, ValueError) as error:
            raise RuntimeError(
                "Formattering is begonnen maar niet volledig afgerond; "
                f"de tag kan gedeeltelijk gewijzigd zijn. Details: {error}"
            ) from error
        emit("complete", message=f"Tag {tag['uid'].hex(':').upper()} is leeggemaakt.")


def run_operation(operation, payload):
    try:
        with redirect_stdout(io.StringIO()):
            if operation == "read":
                run_read()
            elif operation == "write":
                run_write(payload.get("content", ""), payload.get("kind", ""))
            elif operation == "delete":
                run_delete()
            else:
                raise ValueError("Onbekende actie.")
    except (serial.SerialException, TimeoutError, RuntimeError, ValueError) as error:
        emit("error", message=str(error))
    finally:
        global active_port
        active_port = None
        operation_lock.release()
        emit("finished")


def handle_message(message):
    message_type = message.get("type")
    if message_type == "start":
        if not operation_lock.acquire(blocking=False):
            emit("error", message="Er is al een NFC-actie bezig.")
            emit("finished")
            return
        operation = message.get("operation")
        if operation not in ("read", "write", "delete"):
            operation_lock.release()
            emit("error", message="Onbekende actie.")
            emit("finished")
            return
        cancel_event.clear()
        confirm_event.clear()
        threading.Thread(
            target=run_operation,
            args=(operation, message),
            daemon=True,
        ).start()
    elif message_type == "confirm":
        confirm_event.set()
    elif message_type == "cancel":
        cancel_event.set()
        confirm_event.set()


def main():
    for line in sys.stdin:
        try:
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError("Ongeldig bericht.")
            handle_message(message)
        except (json.JSONDecodeError, ValueError) as error:
            emit("error", message=str(error))


if __name__ == "__main__":
    main()
