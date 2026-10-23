import time

import serial

from pn532_common import (
    AuthenticationError,
    CLASSIC_NDEF_BLOCKS,
    DEFAULT_CLASSIC_KEY,
    MAD_KEY,
    NDEF_KEY,
    authenticate_classic,
    find_tag,
    get_mad_ndef_sectors,
    make_classic_ndef_blocks,
    make_mad_blocks,
    open_reader,
    read_classic_block,
    read_classic_ndef,
    write_classic_block,
)

EMPTY_NDEF_TLV = b"\x03\x00\xfe"
NDEF_TRAILER = NDEF_KEY + bytes.fromhex("7F078840") + (b"\xff" * 6)
MAD_TRAILER = MAD_KEY + bytes.fromhex("787788C1") + (b"\xff" * 6)


def wait_for_classic_tag(port):
    print("Houd een lege MIFARE Classic-tag tegen de lezer...")
    while True:
        tag = find_tag(port)
        if tag is not None:
            if tag["sak"] not in (0x08, 0x09, 0x18):
                raise RuntimeError(
                    f"Tag met SAK 0x{tag['sak']:02X} is geen ondersteunde "
                    "MIFARE Classic-tag. Er is niets gewijzigd."
                )
            if len(tag["uid"]) != 4:
                raise RuntimeError(
                    "Alleen Classic-tags met een UID van 4 bytes worden ondersteund."
                )
            return tag
        time.sleep(0.2)


def verify_factory_blank(port, tag):
    target = tag["target"]
    uid = tag["uid"]
    for block in (1, 2, *CLASSIC_NDEF_BLOCKS):
        sector_first = (block // 4) * 4
        authenticate_classic(
            port, target, uid, sector_first, DEFAULT_CLASSIC_KEY
        )
        if read_classic_block(port, target, block) != b"\x00" * 16:
            raise RuntimeError(
                f"Blok {block} bevat al data. Veilig afgebroken; "
                "de tag is niet gewijzigd."
            )


def is_ndef_formatted(port, tag):
    try:
        sectors = get_mad_ndef_sectors(port, tag["target"], tag["uid"])
        if sectors != [1, 2]:
            return False
        for block in (4, 8):
            authenticate_classic(
                port, tag["target"], tag["uid"], block, NDEF_KEY
            )
        return True
    except AuthenticationError:
        return False


def format_factory_classic(port, tag):
    target = tag["target"]
    uid = tag["uid"]
    ndef_blocks = make_classic_ndef_blocks(EMPTY_NDEF_TLV)

    for block, data in zip(CLASSIC_NDEF_BLOCKS, ndef_blocks):
        authenticate_classic(port, target, uid, block, DEFAULT_CLASSIC_KEY)
        write_classic_block(port, target, block, data)

    for trailer_block in (7, 11):
        authenticate_classic(
            port, target, uid, trailer_block, DEFAULT_CLASSIC_KEY
        )
        write_classic_block(port, target, trailer_block, NDEF_TRAILER)

    mad_block1, mad_block2 = make_mad_blocks()
    authenticate_classic(port, target, uid, 0, DEFAULT_CLASSIC_KEY)
    write_classic_block(port, target, 1, mad_block1)
    write_classic_block(port, target, 2, mad_block2)
    write_classic_block(port, target, 3, MAD_TRAILER)


def clear_formatted_ndef(port, tag):
    ndef_blocks = make_classic_ndef_blocks(EMPTY_NDEF_TLV)
    for block, data in zip(CLASSIC_NDEF_BLOCKS, ndef_blocks):
        authenticate_classic(port, tag["target"], tag["uid"], block, NDEF_KEY)
        write_classic_block(port, tag["target"], block, data)


def verify_empty_ndef(port, tag):
    actual = read_classic_ndef(port, tag["target"], tag["uid"])
    expected = b"".join(make_classic_ndef_blocks(EMPTY_NDEF_TLV))
    if actual != expected:
        raise RuntimeError("De controle na formattering is mislukt.")


def main():
    with open_reader() as port:
        tag = wait_for_classic_tag(port)
        print(f"UID: {tag['uid'].hex(':').upper()} | SAK: 0x{tag['sak']:02X}")

        already_formatted = is_ndef_formatted(port, tag)
        if already_formatted:
            print("Deze tag is al NDEF-geformatteerd; bestaande NDEF wordt leeggemaakt.")
        else:
            verify_factory_blank(port, tag)
            print(
                "De relevante gebruikersblokken zijn leeg en gebruiken de "
                "standaard fabrieksleutel."
            )

        confirm = input(
            "Dit maakt een lege NDEF-tag. Data in de gebruikte Classic-sectoren "
            "wordt vervangen. Typ del om door te gaan: "
        )
        if confirm != "del":
            print("Afgebroken; de tag is niet gewijzigd.")
            return

        try:
            if already_formatted:
                clear_formatted_ndef(port, tag)
            else:
                format_factory_classic(port, tag)
            verify_empty_ndef(port, tag)
        except (serial.SerialException, TimeoutError, RuntimeError, ValueError) as error:
            raise RuntimeError(
                "Formattering is begonnen maar niet volledig afgerond; "
                f"de tag kan gedeeltelijk gewijzigd zijn. Details: {error}"
            ) from error

        print("Klaar: de Classic-tag bevat nu een lege NDEF-inhoud.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nGestopt.")
    except (serial.SerialException, TimeoutError, RuntimeError, ValueError) as error:
        print("Formatteerfout:", error)