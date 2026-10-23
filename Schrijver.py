import time
from urllib.parse import urlsplit

import serial

from pn532_common import (
    CLASSIC_NDEF_BLOCKS,
    CLASSIC_NDEF_CAPACITY,
    NDEF_KEY,
    authenticate_classic,
    find_tag,
    get_mad_ndef_sectors,
    make_classic_ndef_blocks,
    make_ndef_tlv,
    open_reader,
    read_classic_ndef,
    write_classic_block,
)


def ask_for_content():
    while True:
        kind = input("Wat wil je schrijven? [1] URL  [2] tekst: ").strip()
        if kind in ("1", "url", "URL"):
            content = input("URL (begin met http:// of https://): ").strip()
            try:
                parsed = urlsplit(content)
                valid_url = (
                    parsed.scheme in ("http", "https")
                    and parsed.hostname is not None
                )
            except ValueError:
                valid_url = False
            if not valid_url:
                print("Voer een geldige URL met http:// of https:// en een host in.")
                continue
            return make_ndef_tlv(content, "url"), content
        if kind in ("2", "tekst", "text", "TEXT"):
            content = input("Tekst: ")
            if not content:
                print("Lege tekst wordt niet geschreven.")
                continue
            return make_ndef_tlv(content, "text"), content
        print("Kies 1 voor URL of 2 voor tekst.")


def wait_for_tag(port):
    print("Houd een MIFARE Classic-tag tegen de lezer...")
    while True:
        tag = find_tag(port)
        if tag is not None:
            return tag
        time.sleep(0.2)


def write_ndef(port, target, uid, ndef_tlv):
    sectors = get_mad_ndef_sectors(port, target, uid)
    if sectors != [1, 2]:
        raise RuntimeError(
            "De Classic-tag is niet met Deleter.py NDEF-klaar gemaakt "
            "of heeft geen NDEF-sectoren 1 en 2."
        )

    if read_classic_ndef(port, target, uid) is None:
        raise RuntimeError("De NDEF-directory van de tag kon niet worden gelezen.")

    padded_blocks = make_classic_ndef_blocks(ndef_tlv)
    if len(padded_blocks) != len(CLASSIC_NDEF_BLOCKS):
        raise RuntimeError("Interne fout: onjuist aantal Classic-blokken.")

    for block, data in zip(CLASSIC_NDEF_BLOCKS, padded_blocks):
        authenticate_classic(port, target, uid, block, NDEF_KEY)
        write_classic_block(port, target, block, data)

    readback = read_classic_ndef(port, target, uid)
    expected = b"".join(padded_blocks)
    if readback != expected:
        raise RuntimeError("De controle na het schrijven is mislukt.")


def main():
    ndef_tlv, content = ask_for_content()
    if len(ndef_tlv) > CLASSIC_NDEF_CAPACITY:
        raise ValueError(
            f"Deze tag heeft {CLASSIC_NDEF_CAPACITY} bytes NDEF-ruimte; "
            f"de invoer vraagt {len(ndef_tlv)} bytes."
        )

    with open_reader() as port:
        tag = wait_for_tag(port)
        target = tag["target"]
        uid = tag["uid"]
        sak = tag["sak"]
        print(f"UID: {uid.hex(':').upper()} | SAK: 0x{sak:02X}")
        if sak not in (0x08, 0x09, 0x18):
            raise RuntimeError(
                "Deze schrijver is voor MIFARE Classic-tags. "
                "Gebruik voor deze tag een passende NDEF-schrijver."
            )

        sectors = get_mad_ndef_sectors(port, target, uid)
        if not {1, 2}.issubset(sectors):
            raise RuntimeError(
                "De tag is nog niet NDEF-klaar. Voer eerst Deleter.py uit "
                "om een lege Classic-tag NDEF-klaar te maken."
            )
        print(f"Inhoud om te schrijven: {content}")
        print(f"NDEF-grootte: {len(ndef_tlv)} / {CLASSIC_NDEF_CAPACITY} bytes.")
        confirm = input(
            "Dit vervangt de huidige NDEF-inhoud. Typ sch om door te gaan: "
        )
        if confirm != "sch":
            print("Afgebroken; de tag is niet gewijzigd.")
            return

        try:
            write_ndef(port, target, uid, ndef_tlv)
        except (serial.SerialException, TimeoutError, RuntimeError, ValueError) as error:
            raise RuntimeError(
                "Schrijven is begonnen maar niet volledig afgerond; "
                f"de NDEF-inhoud kan gedeeltelijk gewijzigd zijn. Details: {error}"
            ) from error
        print("NDEF-inhoud geschreven en teruggelezen ter controle.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nGestopt.")
    except (serial.SerialException, TimeoutError, RuntimeError, ValueError) as error:
        print("Schrijffout:", error)
