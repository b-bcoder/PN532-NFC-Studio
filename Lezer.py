import time

import serial

from pn532_common import (
    decode_ndef_records,
    find_tag,
    ndef_tlv_payload,
    open_reader,
    read_classic_ndef,
    read_type2_memory,
    read_type4_ndef,
    release_target,
)


def describe_tag(sak):
    if sak in (0x08, 0x09, 0x18):
        names = {
            0x08: "MIFARE Classic 1K",
            0x09: "MIFARE Classic Mini",
            0x18: "MIFARE Classic 4K",
        }
        return names[sak]
    if sak & 0x20:
        return "ISO-DEP / NFC Type 4 (bijvoorbeeld DESFire)"
    if sak in (0x00, 0x04):
        return "MIFARE Ultralight / NTAG / NFC Type 2"
    return f"ISO14443-A-tag (SAK 0x{sak:02X})"


def show_ndef(message):
    if not message:
        print("NDEF: geen NDEF-bericht gevonden.")
        return []

    records = decode_ndef_records(message)
    if not records:
        print("NDEF-bericht is leeg.")
        return []

    print("NDEF-records:")
    for index, (kind, value) in enumerate(records, start=1):
        print(f"  {index}. {kind}: {value}")
    return [value for kind, value in records if kind == "URL/URI"]


def read_tag_content(port, tag):
    sak = tag["sak"]
    target = tag["target"]
    uid = tag["uid"]

    try:
        if sak in (0x08, 0x09, 0x18):
            data = read_classic_ndef(port, target, uid)
            if data is None:
                print(
                    "NDEF: geen geldige MAD/NDEF-directory gevonden. "
                    "Er wordt geen volledige sector-dump gemaakt."
                )
                return []
            return show_ndef(ndef_tlv_payload(data))

        if sak & 0x20:
            print("Type 4 NDEF-app en leesbaar NDEF-bestand worden gecontroleerd...")
            return show_ndef(read_type4_ndef(port, target))

        data = read_type2_memory(port, target)
        return show_ndef(ndef_tlv_payload(data))
    except RuntimeError as error:
        print(f"NDEF-inhoud niet beschikbaar: {error}")
        if sak in (0x08, 0x09, 0x18):
            print(
                "Classic-sectoren worden alleen gelezen met de bekende "
                "standaard MAD/NDEF-sleutels; er worden geen sleutels geraden."
            )
        return []


def wait_until_removed(port, target):
    release_target(port, target)
    while True:
        tag = find_tag(port)
        if tag is None:
            return
        release_target(port, tag["target"])
        time.sleep(0.2)


def main():
    print(
        "Lezer voor NFC Type A-tags: Classic, Ultralight/NTAG (Type 2) "
        "en ISO-DEP/Type 4 NDEF."
    )
    print("Houd een tag bij de antenne; Ctrl+C stopt het programma.\n")

    with open_reader() as port:
        while True:
            tag = find_tag(port)
            if tag is None:
                time.sleep(0.2)
                continue

            print("\n--- NFC-tag gevonden ---")
            print(f"Type: {describe_tag(tag['sak'])}")
            print(f"UID:  {tag['uid'].hex(':').upper()}")
            print(f"SAK:  0x{tag['sak']:02X}")
            print(f"ATQA: {tag['atqa'].hex(' ').upper()}")
            if tag["ats"]:
                print(f"ATS:  {tag['ats'].hex(' ').upper()}")
            read_tag_content(port, tag)
            print("Haal de tag weg om de volgende te lezen.")
            wait_until_removed(port, tag["target"])


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nGestopt.")
    except (serial.SerialException, TimeoutError, RuntimeError, ValueError) as error:
        print("Leesfout:", error)