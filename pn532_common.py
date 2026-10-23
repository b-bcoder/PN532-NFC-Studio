import time

import serial
from serial.tools import list_ports

BAUD = 115200
WAKEUP_PREAMBLE = b"\x55\x55" + (b"\x00" * 12)
DEFAULT_CLASSIC_KEY = b"\xff" * 6
MAD_KEY = bytes.fromhex("A0A1A2A3A4A5")
NDEF_KEY = bytes.fromhex("D3F7D3F7D3F7")
MAD_NDEF_AID = b"\x03\xe1"
CLASSIC_NDEF_BLOCKS = (4, 5, 6, 8, 9, 10)
CLASSIC_NDEF_CAPACITY = len(CLASSIC_NDEF_BLOCKS) * 16

URI_PREFIXES = {
    0x00: "",
    0x01: "http://www.",
    0x02: "https://www.",
    0x03: "http://",
    0x04: "https://",
    0x05: "tel:",
    0x06: "mailto:",
    0x07: "ftp://anonymous:anonymous@",
    0x08: "ftp://ftp.",
    0x09: "ftps://",
    0x0A: "sftp://",
    0x0B: "smb://",
    0x0C: "nfs://",
    0x0D: "ftp://",
    0x0E: "dav://",
    0x0F: "news:",
    0x10: "telnet://",
    0x11: "imap:",
    0x12: "rtsp://",
    0x13: "urn:",
    0x14: "pop:",
    0x15: "sip:",
    0x16: "sips:",
    0x17: "tftp:",
    0x18: "btspp://",
    0x19: "btl2cap://",
    0x1A: "btgoep://",
    0x1B: "tcpobex://",
    0x1C: "irdaobex://",
    0x1D: "file://",
    0x1E: "urn:epc:id:",
    0x1F: "urn:epc:tag:",
    0x20: "urn:epc:pat:",
    0x21: "urn:epc:raw:",
    0x22: "urn:epc:",
    0x23: "urn:nfc:",
}


class AuthenticationError(RuntimeError):
    pass


def find_pn532_port():
    ports = [
        port
        for port in list_ports.comports()
        if port.vid == 0x1A86 and port.pid == 0x7523
    ]

    if not ports:
        raise RuntimeError(
            "Geen CH340 USB-serieeladapter gevonden. Controleer de USB-kabel "
            "en welke COM-poort de PN532 gebruikt."
        )
    if len(ports) > 1:
        names = ", ".join(port.device for port in ports)
        raise RuntimeError(
            f"Meerdere CH340-adapters gevonden ({names}); "
            "sluit andere adapters af om de PN532-poort te onderscheiden."
        )
    return ports[0].device


def read_frame(port, timeout=3):
    deadline = time.monotonic() + timeout
    header = bytearray()
    while time.monotonic() < deadline:
        value = port.read(1)
        if not value:
            continue
        header.extend(value)
        if len(header) > 3:
            del header[0]
        if header == b"\x00\x00\xff":
            break
    else:
        raise TimeoutError("Geen PN532-antwoord ontvangen.")

    length = port.read(1)
    checksum = port.read(1)
    if len(length) != 1 or len(checksum) != 1:
        raise TimeoutError("Onvolledig PN532-frame ontvangen.")

    size = length[0]
    if size == 0:
        if checksum[0] != 0xFF or port.read(1) != b"\x00":
            raise RuntimeError("Ongeldig PN532-ACK-frame.")
        return None
    if (size + checksum[0]) & 0xFF:
        raise RuntimeError("Ongeldige lengtechecksum in PN532-frame.")

    rest = port.read(size + 2)
    if len(rest) != size + 2 or rest[-1] != 0:
        raise TimeoutError("Onvolledig PN532-dat frame ontvangen.")
    data = rest[:size]
    if (sum(data) + rest[-2]) & 0xFF:
        raise RuntimeError("Ongeldige datachecksum in PN532-frame.")
    return data


def make_frame(data):
    if not data or len(data) > 255:
        raise ValueError("Ongeldige PN532-opdrachtlengte.")
    length = len(data)
    return (
        b"\x00\x00\xff"
        + bytes((length, (-length) & 0xFF))
        + data
        + bytes(((-sum(data)) & 0xFF, 0x00))
    )


def send_command(port, command, *parameters):
    port.write(make_frame(bytes((0xD4, command, *parameters))))
    try:
        ack = read_frame(port)
    except TimeoutError as error:
        raise TimeoutError(
            f"Geen ACK van de PN532 voor opdracht 0x{command:02X}."
        ) from error
    if ack is not None:
        raise RuntimeError("De PN532 stuurde geen geldig ACK-frame.")

    try:
        response = read_frame(port)
    except TimeoutError as error:
        raise TimeoutError(
            f"Geen antwoord van de PN532 op opdracht 0x{command:02X}."
        ) from error
    if response is None or len(response) < 2:
        raise RuntimeError("Leeg PN532-antwoord ontvangen.")
    if response[:2] != bytes((0xD5, command + 1)):
        raise RuntimeError(f"Onverwacht PN532-antwoord: {response.hex(' ')}")
    return response[2:]


def open_reader():
    port_name = find_pn532_port()
    port = serial.Serial(port_name, BAUD, timeout=0.2)
    try:
        port.reset_input_buffer()
        port.write(WAKEUP_PREAMBLE + make_frame(b"\xD4\x02"))
        if read_frame(port) is not None:
            raise RuntimeError("De PN532 stuurde geen firmware-ACK.")

        firmware = read_frame(port)
        if not firmware or firmware[:2] != b"\xD5\x03" or len(firmware) < 5:
            raise RuntimeError(f"Ongeldig firmware-antwoord: {firmware!r}")

        send_command(port, 0x14, 0x01, 0x14, 0x01)
        send_command(port, 0x32, 0x05, 0x00, 0x00, 0x01)
        print(f"Verbonden met PN532 v{firmware[3]}.{firmware[4]} via {port_name}.")
        return port
    except Exception:
        port.close()
        raise


def in_data_exchange(port, target, *data):
    response = send_command(port, 0x40, target, *data)
    if not response:
        raise RuntimeError("PN532-antwoord bevat geen kaartstatus.")
    if response[0] != 0:
        status = response[0]
        explanations = {
            0x01: "time-out bij communicatie met de tag",
            0x02: "CRC- of pariteitsfout",
            0x04: "communicatiebuffer is niet goed afgehandeld",
            0x05: "tag heeft de opdracht afgewezen",
            0x13: "kaartopdracht mislukt; controleer tagtype, sectorselectie en authenticatie",
            0x14: "MIFARE Classic-authenticatie mislukt",
        }
        detail = explanations.get(status, "onbekende kaartfout")
        raise RuntimeError(f"Kaartopdracht mislukt (0x{status:02X}): {detail}.")
    return response[1:]


def find_tag(port):
    response = send_command(port, 0x4A, 0x01, 0x00)
    if not response:
        raise RuntimeError("Ongeldig antwoord bij kaartdetectie.")
    if response[0] == 0:
        return None
    if response[0] != 1 or len(response) < 7:
        raise RuntimeError(f"Onverwacht kaartdetectie-antwoord: {response.hex(' ')}")

    target = response[1]
    atqa = response[2:4]
    sak = response[4]
    uid_length = response[5]
    uid_end = 6 + uid_length
    if uid_length == 0 or len(response) < uid_end:
        raise RuntimeError("Ongeldige UID ontvangen van de PN532.")
    uid = response[6:uid_end]
    ats = b""
    if sak & 0x20 and len(response) > uid_end:
        ats_length = response[uid_end]
        ats = response[uid_end + 1:uid_end + 1 + ats_length]
    return {
        "target": target,
        "uid": uid,
        "sak": sak,
        "atqa": atqa,
        "ats": ats,
    }


def release_target(port, target):
    response = send_command(port, 0x52, target)
    if not response or response[0] not in (0x00, 0x01):
        raise RuntimeError(
            f"PN532 kon target {target} niet vrijgeven: {response.hex(' ')}"
        )


def authenticate_classic(port, target, uid, block, key):
    if len(uid) != 4:
        raise RuntimeError("MIFARE Classic-authenticatie vereist een UID van 4 bytes.")
    if len(key) != 6:
        raise ValueError("Een MIFARE Classic-sleutel moet precies 6 bytes bevatten.")
    result = send_command(port, 0x40, target, 0x60, block, *key, *uid)
    if result != b"\x00":
        raise AuthenticationError(
            f"Authenticatie op Classic-blok {block} mislukt."
        )


def read_classic_block(port, target, block):
    data = in_data_exchange(port, target, 0x30, block)
    if len(data) != 16:
        raise RuntimeError(f"Classic-blok {block} gaf geen 16 bytes terug.")
    return data


def write_classic_block(port, target, block, data):
    if len(data) != 16:
        raise ValueError("Een MIFARE Classic-blok moet precies 16 bytes bevatten.")
    in_data_exchange(port, target, 0xA0, block, *data)


def classic_sector_trailer(sector):
    if sector < 32:
        return sector * 4 + 3
    return 128 + (sector - 32) * 16 + 15


def classic_sector_first_block(sector):
    if sector < 32:
        return sector * 4
    return 128 + (sector - 32) * 16


def mad_crc8(data):
    crc = 0xC7
    for value in data:
        crc ^= value
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x1D) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def make_mad_blocks(ndef_sectors=(1, 2)):
    if not ndef_sectors or any(not 1 <= sector <= 15 for sector in ndef_sectors):
        raise ValueError("MAD1-sectoren moeten tussen 1 en 15 liggen.")
    aids = bytearray(30)
    for sector in ndef_sectors:
        index = (sector - 1) * 2
        aids[index:index + 2] = MAD_NDEF_AID
    body = bytes((0x01,)) + bytes(aids)
    block1 = bytes((mad_crc8(body), 0x01)) + bytes(aids[:14])
    return block1, bytes(aids[14:])


def get_mad_ndef_sectors(port, target, uid):
    authenticate_classic(port, target, uid, 0, MAD_KEY)
    block1 = read_classic_block(port, target, 1)
    block2 = read_classic_block(port, target, 2)
    aids = block1[2:] + block2
    info = block1[1]
    expected_crc = mad_crc8(bytes((info,)) + aids)
    if block1[0] != expected_crc:
        raise RuntimeError("MAD-directory heeft een ongeldige CRC.")

    return [
        sector
        for sector in range(1, 16)
        if aids[(sector - 1) * 2:(sector - 1) * 2 + 2] == MAD_NDEF_AID
    ]


def make_classic_ndef_blocks(ndef_tlv):
    if len(ndef_tlv) > CLASSIC_NDEF_CAPACITY:
        raise ValueError(
            f"NDEF-inhoud is {len(ndef_tlv)} bytes; de tag biedt "
            f"{CLASSIC_NDEF_CAPACITY} bytes in de gebruikte sectoren."
        )
    padded = ndef_tlv.ljust(CLASSIC_NDEF_CAPACITY, b"\x00")
    return tuple(
        padded[offset:offset + 16]
        for offset in range(0, CLASSIC_NDEF_CAPACITY, 16)
    )


def read_classic_ndef(port, target, uid):
    authenticate_classic(port, target, uid, 0, MAD_KEY)
    block1 = read_classic_block(port, target, 1)
    block2 = read_classic_block(port, target, 2)
    aids = block1[2:] + block2
    if block1[0] != mad_crc8(bytes((block1[1],)) + aids):
        return None

    sectors = [
        sector
        for sector in range(1, 16)
        if aids[(sector - 1) * 2:(sector - 1) * 2 + 2] == MAD_NDEF_AID
    ]
    if not sectors:
        return None

    data = bytearray()
    for sector in sectors:
        first_block = classic_sector_first_block(sector)
        authenticate_classic(port, target, uid, first_block, NDEF_KEY)
        for offset in range(3):
            data.extend(read_classic_block(port, target, first_block + offset))
    return bytes(data)


def read_type2_memory(port, target):
    cc_response = in_data_exchange(port, target, 0x30, 3)
    if len(cc_response) != 16 or cc_response[0] != 0xE1:
        raise RuntimeError("Geen geldige Type 2 Capability Container gevonden.")
    capacity = cc_response[2] * 8
    if capacity <= 0:
        raise RuntimeError("De Type 2-tag meldt geen gebruikersgeheugen.")

    data = bytearray()
    page_count = (capacity + 3) // 4
    for page_offset in range(0, page_count, 4):
        page = 4 + page_offset
        data.extend(in_data_exchange(port, target, 0x30, page))
    return bytes(data[:capacity])


def select_type4_file(port, target, file_id):
    response = in_data_exchange(
        port, target, 0x00, 0xA4, 0x00, 0x0C, 0x02, *file_id
    )
    if response[-2:] != b"\x90\x00":
        raise RuntimeError(f"Type 4-bestand selecteren mislukt: {response.hex(' ')}")


def type4_read_binary(port, target, offset, length):
    if not 1 <= length <= 0xF0 or not 0 <= offset <= 0xFFFF:
        raise ValueError("Ongeldige lengte of offset voor Type 4 ReadBinary.")
    response = in_data_exchange(
        port,
        target,
        0x00,
        0xB0,
        (offset >> 8) & 0xFF,
        offset & 0xFF,
        length,
    )
    if len(response) < 2 or response[-2:] != b"\x90\x00":
        raise RuntimeError(f"Type 4 ReadBinary mislukt: {response.hex(' ')}")
    return response[:-2]


def read_type4_ndef(port, target):
    response = in_data_exchange(
        port,
        target,
        0x00,
        0xA4,
        0x04,
        0x00,
        0x07,
        0xD2,
        0x76,
        0x00,
        0x00,
        0x85,
        0x01,
        0x01,
        0x00,
    )
    if response[-2:] != b"\x90\x00":
        raise RuntimeError(f"Type 4 NDEF-app niet beschikbaar: {response.hex(' ')}")

    select_type4_file(port, target, b"\xE1\x03")
    header = type4_read_binary(port, target, 0, 15)
    cc_length = int.from_bytes(header[:2], "big")
    if cc_length < 7 or cc_length > 4096:
        raise RuntimeError(f"Ongeldige Type 4 Capability Container-lengte: {cc_length}.")
    cc = bytearray(header[:min(cc_length, len(header))])
    while len(cc) < cc_length:
        count = min(0xF0, cc_length - len(cc))
        cc.extend(type4_read_binary(port, target, len(cc), count))

    ndef_file = None
    max_size = 0
    offset = 7
    while offset < len(cc):
        tlv_type = cc[offset]
        offset += 1
        if tlv_type == 0x00:
            continue
        if tlv_type == 0xFE:
            break
        if offset >= len(cc):
            raise RuntimeError("Afgebroken Type 4-bestandscontrole.")
        tlv_length = cc[offset]
        offset += 1
        value = cc[offset:offset + tlv_length]
        if len(value) < tlv_length:
            raise RuntimeError("Afgebroken Type 4-bestandscontrole.")
        offset += tlv_length
        if tlv_type == 0x04 and tlv_length >= 6:
            ndef_file = value[:2]
            max_size = int.from_bytes(value[2:4], "big")
            break

    if ndef_file is None or max_size < 2:
        raise RuntimeError("Geen NDEF-bestand in de Type 4-tag gevonden.")

    select_type4_file(port, target, ndef_file)
    nlen = int.from_bytes(type4_read_binary(port, target, 0, 2), "big")
    if nlen > max_size - 2:
        raise RuntimeError("Type 4-tag meldt een ongeldige NDEF-lengte.")

    content = bytearray()
    while len(content) < nlen:
        count = min(0xF0, nlen - len(content))
        content.extend(type4_read_binary(port, target, 2 + len(content), count))
    return bytes(content)


def ndef_tlv_payload(data):
    offset = 0
    while offset < len(data):
        tlv_type = data[offset]
        offset += 1
        if tlv_type == 0x00:
            continue
        if tlv_type == 0xFE:
            return None
        if offset >= len(data):
            raise RuntimeError("Afgebroken NFC TLV-lengte.")
        length = data[offset]
        offset += 1
        if length == 0xFF:
            if offset + 2 > len(data):
                raise RuntimeError("Afgebroken uitgebreide NFC TLV-lengte.")
            length = int.from_bytes(data[offset:offset + 2], "big")
            offset += 2
        end = offset + length
        if end > len(data):
            raise RuntimeError("NFC TLV-inhoud loopt buiten het gelezen geheugen.")
        value = data[offset:end]
        offset = end
        if tlv_type == 0x03:
            return value
    return None


def decode_ndef_records(message):
    records = []
    offset = 0
    while offset < len(message):
        header = message[offset]
        offset += 1
        if header & 0x20:
            raise RuntimeError("Gechunkte NDEF-records worden niet ondersteund.")
        if offset >= len(message):
            raise RuntimeError("Afgebroken NDEF-recordheader.")
        short_record = bool(header & 0x10)
        has_id = bool(header & 0x08)
        type_length = message[offset]
        offset += 1

        if short_record:
            if offset >= len(message):
                raise RuntimeError("Afgebroken NDEF-payloadlengte.")
            payload_length = message[offset]
            offset += 1
        else:
            if offset + 4 > len(message):
                raise RuntimeError("Afgebroken uitgebreide NDEF-payloadlengte.")
            payload_length = int.from_bytes(message[offset:offset + 4], "big")
            offset += 4

        if has_id and offset >= len(message):
            raise RuntimeError("Afgebroken NDEF-ID-lengte.")
        id_length = message[offset] if has_id else 0
        if has_id:
            offset += 1
        end_type = offset + type_length
        end_id = end_type + id_length
        end_payload = end_id + payload_length
        if end_payload > len(message):
            raise RuntimeError("NDEF-record loopt buiten de berichtlengte.")

        record_type = message[offset:end_type]
        payload = message[end_id:end_payload]
        offset = end_payload

        tnf = header & 0x07
        if tnf == 0x01 and record_type == b"U" and payload:
            prefix = URI_PREFIXES.get(payload[0], f"[URI-prefix 0x{payload[0]:02X}]")
            value = prefix + payload[1:].decode("utf-8", errors="replace")
            records.append(("URL/URI", value))
        elif tnf == 0x01 and record_type == b"T" and payload:
            status = payload[0]
            lang_length = status & 0x3F
            if 1 + lang_length <= len(payload):
                language = payload[1:1 + lang_length].decode("ascii", errors="replace")
                text = payload[1 + lang_length:].decode(
                    "utf-16" if status & 0x80 else "utf-8",
                    errors="replace",
                )
                records.append((f"Tekst ({language})", text))
            else:
                records.append(("Tekst", payload.hex(" ")))
        elif tnf == 0x02:
            records.append((f"MIME {record_type.decode('ascii', errors='replace')}", payload.hex(" ")))
        else:
            records.append((f"NDEF TNF {tnf}, type {record_type.hex(' ')}", payload.hex(" ")))

        if header & 0x40:
            break
    return records


def make_ndef_tlv(content, kind):
    if kind == "url":
        prefixes = (
            ("https://www.", 0x02),
            ("http://www.", 0x01),
            ("https://", 0x04),
            ("http://", 0x03),
        )
        prefix_code = 0
        suffix = content
        for prefix, code in prefixes:
            if content.startswith(prefix):
                prefix_code = code
                suffix = content[len(prefix):]
                break
        payload = bytes((prefix_code,)) + suffix.encode("utf-8")
        record_type = b"U"
    elif kind == "text":
        text = content.encode("utf-8")
        payload = b"\x02en" + text
        record_type = b"T"
    else:
        raise ValueError("Kies 'url' of 'text'.")

    if len(payload) > 255:
        raise ValueError("De invoer is te lang voor dit NFC-bericht.")
    record = bytes((0xD1, 0x01, len(payload))) + record_type + payload
    if len(record) > 254:
        raise ValueError("De NDEF-record is te lang voor dit script.")
    return bytes((0x03, len(record))) + record + b"\xfe"
