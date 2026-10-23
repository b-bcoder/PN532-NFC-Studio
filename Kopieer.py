import time

import serial

from pn532_common import (
    AuthenticationError,
    CLASSIC_NDEF_BLOCKS,
    CLASSIC_NDEF_CAPACITY,
    NDEF_KEY,
    authenticate_classic,
    find_tag,
    get_mad_ndef_sectors,
    make_classic_ndef_blocks,
    ndef_tlv_payload,
    open_reader,
    read_classic_ndef,
    read_type2_memory,
    read_type4_ndef,
    release_target,
    write_classic_block,
)


def wait_for_tag(port, prompt):
    print(prompt)
    while True:
        tag = find_tag(port)
        if tag is not None:
            return tag
        time.sleep(0.2)


def wait_until_removed(port, target):
    release_target(port, target)
    while True:
        tag = find_tag(port)
        if tag is None:
            return
        release_target(port, tag["target"])
        time.sleep(0.2)


def read_ndef_message(port, tag):
    sak = tag["sak"]
    target = tag["target"]

    if sak in (0x08, 0x09, 0x18):
        data = read_classic_ndef(port, target, tag["uid"])
        if data is None:
            raise RuntimeError("Geen leesbare NDEF-directory op de brontag.")
        message = ndef_tlv_payload(data)
    elif sak & 0x20:
        message = read_type4_ndef(port, target)
    elif sak in (0x00, 0x04):
        message = ndef_tlv_payload(read_type2_memory(port, target))
    else:
        raise RuntimeError(
            f"Brontag met SAK 0x{sak:02X} wordt niet ondersteund."
        )

    if not message:
        raise RuntimeError(
            "De brontag bevat geen leesbaar NDEF-bericht. "
            "Tags die alleen een UID of beveiligde gegevens gebruiken "
            "kunnen hiermee niet worden gekopieerd."
        )
    return message


def write_classic_ndef(port, tag, message):
    target = tag["target"]
    uid = tag["uid"]
    sectors = validate_classic_destination(port, tag)
    if sectors != [1, 2]:
        raise RuntimeError(
            "Doeltag is niet NDEF-klaar met sectoren 1 en 2. "
            "Er is niets geschreven."
        )

    if len(message) > CLASSIC_NDEF_CAPACITY - 3:
        raise ValueError(
            f"Het bericht is {len(message)} bytes; deze doeltag biedt "
            f"maximaal {CLASSIC_NDEF_CAPACITY - 3} bytes NDEF-inhoud."
        )

    ndef_tlv = bytes((0x03, len(message))) + message + b"\xfe"
    blocks = make_classic_ndef_blocks(ndef_tlv)
    try:
        for block, data in zip(CLASSIC_NDEF_BLOCKS, blocks):
            authenticate_classic(port, target, uid, block, NDEF_KEY)
            write_classic_block(port, target, block, data)

        written = read_classic_ndef(port, target, uid)
        if written is None or ndef_tlv_payload(written) != message:
            raise RuntimeError("Teruglezen van de doeltag komt niet overeen.")
    except (serial.SerialException, TimeoutError, RuntimeError, ValueError) as error:
        raise RuntimeError(
            "Schrijven is begonnen maar niet volledig afgerond; "
            f"de NDEF-inhoud kan gedeeltelijk gewijzigd zijn. Details: {error}"
        ) from error


def validate_classic_destination(port, tag):
    try:
        sectors = get_mad_ndef_sectors(port, tag["target"], tag["uid"])
    except AuthenticationError as error:
        raise RuntimeError(
            "De doeltag is niet NDEF-geformatteerd met de vereiste MAD-sleutel. "
            "Gebruik Deleter.py alleen om een lege MIFARE Classic-doeltag "
            "NDEF-klaar te maken; er is niets gewijzigd."
        ) from error

    if sectors != [1, 2]:
        raise RuntimeError(
            "Doeltag is niet NDEF-klaar met sectoren 1 en 2. "
            "Er is niets gewijzigd."
        )
    if read_classic_ndef(port, tag["target"], tag["uid"]) is None:
        raise RuntimeError(
            "De NDEF-directory van de doeltag kan niet worden gelezen; "
            "er is niets gewijzigd."
        )
    return sectors


def main():
    print(
        "Kopieert alleen gewone NDEF-berichtinhoud naar een "
        "NDEF-geformatteerde MIFARE Classic-doeltag."
    )
    print(
        "UID's, beveiligingssleutels en toegangsrechten worden niet "
        "gelezen of gekopieerd."
    )

    with open_reader() as port:
        source = wait_for_tag(port, "Houd de brontag bij de lezer...")
        print(
            f"Brontag gevonden: SAK 0x{source['sak']:02X} | "
            f"UID {source['uid'].hex(':').upper()}"
        )
        message = read_ndef_message(port, source)
        print(f"NDEF-bericht gelezen ({len(message)} bytes).")

        print("Haal de brontag weg.")
        wait_until_removed(port, source["target"])
        destination = wait_for_tag(
            port, "Houd nu de NDEF-geformatteerde Classic-doeltag bij de lezer..."
        )
        if destination["uid"] == source["uid"]:
            raise RuntimeError("Dit is dezelfde tag; er is niets geschreven.")
        if destination["sak"] not in (0x08, 0x09, 0x18):
            raise RuntimeError(
                "De doeltag moet een MIFARE Classic-tag zijn. "
                "Er is niets geschreven."
            )

        print(
            f"Doeltag gevonden: SAK 0x{destination['sak']:02X} | "
            f"UID {destination['uid'].hex(':').upper()}"
        )
        validate_classic_destination(port, destination)
        confirm = input(
            "De bestaande NDEF-inhoud van de doeltag wordt vervangen. "
            "Typ KOPIEER om door te gaan: "
        )
        if confirm != "KOPIEER":
            print("Afgebroken; de doeltag is niet gewijzigd.")
            return

        write_classic_ndef(port, destination, message)

        print("Klaar: NDEF-inhoud gekopieerd en gecontroleerd.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nGestopt.")
    except (serial.SerialException, TimeoutError, RuntimeError, ValueError) as error:
        print("Kopieerfout:", error)