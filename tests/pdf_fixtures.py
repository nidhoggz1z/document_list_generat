"""Tiny synthetic PDFs, built without a second PDF or image dependency."""
import hashlib
import struct
import zlib


def _rc4(key, data):
    state = list(range(256))
    j = 0
    for i in range(256):
        j = (j + state[i] + key[i % len(key)]) % 256
        state[i], state[j] = state[j], state[i]
    i = j = 0
    result = bytearray()
    for value in data:
        i = (i + 1) % 256
        j = (j + state[i]) % 256
        state[i], state[j] = state[j], state[i]
        result.append(value ^ state[(state[i] + state[j]) % 256])
    return bytes(result)


def make_pdf(kinds=('text', 'scan', 'mixed', 'overlay'), *, password=None,
             broken_page=None):
    """Return a complete xref PDF; encrypted fixtures use standard revision 2."""
    objects = [b'', b'', b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    pixels = b'\xff\x00\x00\x00\xff\x00\x00\x00\xff\xff\xff\xff'
    objects.append(b'<< /Type /XObject /Subtype /Image /Width 2 /Height 2 '
                   b'/ColorSpace /DeviceRGB /BitsPerComponent 8 /Length 12 >>\nstream\n'
                   + pixels + b'\nendstream')
    pages = []
    for index, kind in enumerate(kinds, 1):
        commands = b''
        if kind in ('scan', 'mixed', 'overlay'):
            commands += b'q 140 0 0 100 20 70 cm /Im0 Do Q\n'
        if kind in ('text', 'mixed', 'overlay'):
            commands += (b'BT /F1 12 Tf ' + (b'3 Tr ' if kind == 'overlay' else b'')
                         + b'20 180 Td (Page ' + str(index).encode() + b' ' + kind.encode()
                         + b') Tj 0 -20 Td (Product    Amount) Tj '
                         b'0 -20 Td (Loan       100) Tj ET\n')
        commands += b'10 10 m 180 10 l 180 190 l S\n'
        stream_id = len(objects) + 1
        objects.append(b'<< /Length ' + str(len(commands)).encode() + b' >>\nstream\n'
                       + commands + b'endstream')
        page_id = len(objects) + 1
        pages.append(str(page_id).encode() + b' 0 R')
        objects.append(b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] '
                       b'/Resources << /Font << /F1 3 0 R >> /XObject << /Im0 4 0 R >> >> '
                       b'/Contents ' + str(stream_id).encode() + b' 0 R >>'
                       if index != broken_page else b'null')
    objects[0] = b'<< /Type /Catalog /Pages 2 0 R >>'
    objects[1] = (b'<< /Type /Pages /Count ' + str(len(pages)).encode()
                  + b' /Kids [' + b' '.join(pages) + b'] >>')
    trailer = b'/Root 1 0 R'
    if password is not None:
        pad = bytes.fromhex('28bf4e5e4e758a4164004e56fffa01082e2e00b6d0683e802f0ca9fe6453697a')
        padded = (password.encode() + pad)[:32]
        owner = _rc4(hashlib.md5((b'owner' + pad)[:32]).digest()[:5], padded)
        file_id = b'fixture-id-123456'
        key = hashlib.md5(padded + owner + struct.pack('<i', -4) + file_id).digest()[:5]
        user = _rc4(key, pad)
        for number, obj in enumerate(objects, 1):
            if b'\nstream\n' in obj:
                head, rest = obj.split(b'\nstream\n', 1)
                data, tail = rest.rsplit(b'\nendstream', 1) if b'\nendstream' in rest else rest.rsplit(b'endstream', 1)
                obj_key = hashlib.md5(key + struct.pack('<I', number)[:3] + b'\0\0').digest()[:10]
                objects[number - 1] = head + b'\nstream\n' + _rc4(obj_key, data) + b'\nendstream' + tail
        objects.append(b'<< /Filter /Standard /V 1 /R 2 /Length 40 /P -4 /O <'
                       + owner.hex().encode() + b'> /U <' + user.hex().encode() + b'> >>')
        trailer += (b' /Encrypt ' + str(len(objects)).encode() + b' 0 R /ID [<'
                    + file_id.hex().encode() + b'><' + file_id.hex().encode() + b'>]')
    output = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output += str(number).encode() + b' 0 obj\n' + obj + b'\nendobj\n'
    xref = len(output)
    output += b'xref\n0 ' + str(len(offsets)).encode() + b'\n0000000000 65535 f \n'
    for offset in offsets[1:]:
        output += f'{offset:010} 00000 n \n'.encode()
    output += (b'trailer\n<< /Size ' + str(len(offsets)).encode() + b' ' + trailer
               + b' >>\nstartxref\n' + str(xref).encode() + b'\n%%EOF\n')
    return bytes(output)


def png_pixels(data):
    """Validate PNG checksums and decode RGB rows for render assertions."""
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    offset, packed = 8, bytearray()
    while offset < len(data):
        size = struct.unpack('>I', data[offset:offset + 4])[0]
        tag = data[offset + 4:offset + 8]
        body = data[offset + 8:offset + 8 + size]
        assert zlib.crc32(tag + body) == struct.unpack('>I', data[offset + 8 + size:offset + 12 + size])[0]
        if tag == b'IHDR':
            width, height, depth, color, *_ = struct.unpack('>IIBBBBB', body)
            assert (depth, color) == (8, 2)
        elif tag == b'IDAT':
            packed += body
        offset += size + 12
    rows = zlib.decompress(packed)
    assert len(rows) == height * (width * 3 + 1)
    assert all(rows[y * (width * 3 + 1)] == 0 for y in range(height))
    return width, height, rows
