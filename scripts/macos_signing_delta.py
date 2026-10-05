"""Conservative, structural Mach-O signing-delta evidence; never a trust gate.

The caller must independently verify the ORIGINAL bytes against its trusted
unsigned-build provenance before calling. "Unsigned" release inputs can already
contain upstream/linker signatures. This helper supports replacing such an
existing LC_CODE_SIGNATURE at its unchanged terminal offset, or appending one
16-byte command in verified zero header padding of an unsigned x86_64 slice.
Only the observed BE FAT32 x86_64/arm64 two-slice envelope is supported. No code
or data is moved within a slice; 32-bit, other FAT and big-endian thin formats
are rejected. Nothing here establishes that any signature is valid.

Supported: little-endian arm64/x86_64 MH_EXECUTE/MH_DYLIB/MH_BUNDLE with bounded,
recognized load commands, a nonoverlapping final read-only __LINKEDIT, and a
framed embedded-signature SuperBlob. Opaque signature component contents are NOT
validated. Unknown commands, encrypted/fileset/thread-command layouts and
signature relocation are refused, even if Apple's tools might accept them.
No guarantee is made that every nested file in the D534 observation fits this
subset. No files, processes, keychains, network or candidate code are accessed.

Allowed byte differences are exactly the existing signature allocation,
LC_CODE_SIGNATURE.datasize, __LINKEDIT.filesize, and exactly
__LINKEDIT.vmsize = round_up(new filesize, 16384), even when shrinking. This is
the source-pinned modern Apple internal allocator contract, not a heuristic.
For signature addition only ncmds/sizeofcmds grow by one command/16 bytes;
new terminal alignment padding is zero and less than 16 bytes. FAT offsets/sizes
are recomputed by minimal 16 KiB packing with every fat_arch.align set to 14,
with zero header/inter-slice padding and no trailing bytes. All remaining
header/load-command bookkeeping, offsets, padding, code and data stay byte-exact. This deliberately rejects allocator variants which relocate
existing signatures, resize string-table padding, or use other VM/FAT alignment.
The prospective signature allocation must be a multiple of 16 bytes.

Format references (Apple primary sources, no third-party parser dependency):
https://github.com/apple-oss-distributions/xnu/blob/main/EXTERNAL_HEADERS/mach-o/loader.h
https://github.com/apple-oss-distributions/Security/blob/db15acbe6a7f257a859ad9a3bb86097bfe0679d9/OSX/libsecurity_codesigning/lib/codesign_alloc.cpp
https://github.com/apple-oss-distributions/Security/blob/main/OSX/libsecurity_codesigning/lib/sigblob.h
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import struct


ALLOCATOR_CONTRACT = 'apple-security-db15acbe6a7f257a859ad9a3bb86097bfe0679d9-internal-v1'

MAX_IMAGE_BYTES = 512 * 1024 * 1024
MAX_SIGNATURE_BYTES = 16 * 1024 * 1024
MAX_COMMANDS = 4096
MAX_COMMAND_BYTES = 4 * 1024 * 1024
LC_SEGMENT_64 = 0x19
LC_CODE_SIGNATURE = 0x1D
_LINKEDIT_DATA = frozenset((0x1E, 0x26, 0x29, 0x2B, 0x2E, 0x80000033, 0x80000034))
_DYLIB_COMMANDS = frozenset((0xC, 0xD, 0x80000018, 0x8000001F, 0x20, 0x80000023))
_STRING_COMMANDS = frozenset((0xE, 0xF, 0x8000001C))
_FAT_MAGIC = frozenset((b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca',
                         b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'))


def _require(condition, reason):
    if not condition:
        raise ValueError('macho_delta_' + reason)


def _fixed_name(raw):
    name, separator, padding = raw.partition(b'\0')
    _require(bool(name) and (not separator or not any(padding)), 'invalid_name')
    return name


def _disjoint(ranges, reason):
    end = 0
    for start, stop in sorted(ranges):
        _require(start >= end and stop > start, reason)
        end = stop


def _inline_string(raw, minimum):
    _require(len(raw) >= minimum, 'command_size')
    offset = struct.unpack_from('<I', raw, 8)[0]
    _require(minimum <= offset < len(raw) and not any(raw[minimum:offset]), 'command_string')
    end = raw.find(b'\0', offset)
    _require(end > offset and not any(raw[end:]), 'command_string')


def _signature_frame(raw):
    """Check declared SuperBlob framing, NOT cryptography or allocation slack.

    Apple's writer writes blob.length() bytes into a larger allocated signature
    region; its reader treats LC datasize as a maximum. Bytes after that declared
    length can retain old signature data. They stay inside the bounded signature
    allocation and in the full-file hashes; they are not authenticated code.
    See the fixed-version writer/reader references in SIGNING_PREPARATION.md.
    """
    _require(12 <= len(raw) <= MAX_SIGNATURE_BYTES, 'signature_size')
    magic, length, count = struct.unpack_from('>III', raw)
    _require(magic == 0xFADE0CC0 and 1 <= count <= 128
             and 12 + count * 8 <= length <= len(raw), 'signature_frame')
    spans, slots = [], set()
    for index in range(count):
        slot, offset = struct.unpack_from('>II', raw, 12 + 8 * index)
        _require(slot not in slots and 12 + count * 8 <= offset <= length - 8,
                 'signature_index')
        slots.add(slot)
        blob_magic, size = struct.unpack_from('>II', raw, offset)
        _require(size >= 8 and offset + size <= length, 'signature_component')
        if slot == 0:
            _require(blob_magic == 0xFADE0C02 and size >= 44, 'signature_code_directory')
        spans.append((offset, offset + size))
    _require(0 in slots, 'signature_code_directory')
    _disjoint(spans, 'signature_overlap')
    def require_zero(start, stop, region):
        # Structural diagnostics only: never include signature/payload bytes.
        # Internal framing remains strict; allocation slack is separate.
        first_nonzero = next((index for index in range(start, stop) if raw[index]), None)
        if first_nonzero is not None:
            nonzero_count = sum(bool(value) for value in raw[start:stop])
            raise ValueError('macho_delta_signature_unframed_bytes: '
                f'region={region}, region_start={start}, region_end={stop}, '
                f'first_nonzero={first_nonzero}, nonzero_count={nonzero_count}, '
                f'superblob_length={length}, component_count={count}, '
                f'allocation_bytes={len(raw)}')
    cursor = 12 + count * 8
    for start, stop in sorted(spans):
        require_zero(cursor, start, 'superblob_internal_gap')
        cursor = stop
    require_zero(cursor, length, 'superblob_internal_tail')
    # Do not require raw[length:] to be zero. No byte outside LC_CODE_SIGNATURE
    # is exempted: _verify_thin still compares every original payload range,
    # and signed_sha256 plus the final inventory hash this complete allocation.


@dataclass(frozen=True)
class _Layout:
    cpu: int
    subtype: int
    command_count: int
    commands_end: int
    signature_command: int | None
    signature_offset: int
    signature_size: int
    linkedit_command: int
    linkedit_offset: int
    linkedit_size: int
    linkedit_vm_size: int


def _layout(data, *, allow_missing=False):
    _require(type(data) is bytes, 'bytes_required')
    _require(32 <= len(data) <= MAX_IMAGE_BYTES, 'image_size')
    _require(data[:4] not in _FAT_MAGIC, 'fat_unsupported')
    _require(data[:4] == b'\xcf\xfa\xed\xfe', 'format_unsupported')
    _, cpu, subtype, filetype, ncmds, cmdbytes, flags, reserved = struct.unpack_from('<8I', data)
    _require(cpu in (0x0100000C, 0x01000007), 'cpu_unsupported')
    _require((cpu == 0x0100000C and subtype in (0, 1, 2, 0x80000002))
             or (cpu == 0x01000007 and subtype in (3, 8, 0x80000003)), 'cpu_unsupported')
    _require(filetype in (2, 6, 8) and reserved == 0, 'filetype_unsupported')
    _require(1 <= ncmds <= MAX_COMMANDS and ncmds * 8 <= cmdbytes <= MAX_COMMAND_BYTES
             and cmdbytes % 8 == 0 and 32 + cmdbytes <= len(data), 'load_commands')
    commands_end = 32 + cmdbytes
    seen, segments, sections, link_ranges = set(), [], [], []
    section_vm_ranges, zero_file_padding = [], []
    signature = symtab = dysymtab = entry = None
    cursor = 32
    for _ in range(ncmds):
        _require(cursor + 8 <= commands_end, 'load_commands')
        command, size = struct.unpack_from('<II', data, cursor)
        _require(size >= 8 and size % 8 == 0 and cursor + size <= commands_end, 'command_size')
        raw = data[cursor:cursor + size]
        if command not in (_DYLIB_COMMANDS - {0xD}) | {LC_SEGMENT_64, 0x8000001C}:
            _require(command not in seen, 'duplicate_command')
        seen.add(command)
        if command == LC_SEGMENT_64:
            _require(size >= 72, 'segment_size')
            _, _, name, addr, vmsize, offset, count, maxprot, initprot, nsects, segflags = struct.unpack_from('<II16sQQQQiiII', raw)
            name = _fixed_name(name)
            _require(size == 72 + nsects * 80 and nsects <= MAX_COMMANDS, 'segment_size')
            _require(addr + vmsize < 2**64 and count <= vmsize
                     and offset <= len(data) and offset + count <= len(data)
                     and not (segflags & ~0x10) and 0 <= initprot <= maxprot <= 7
                     and not (initprot & ~maxprot),
                     'segment_bounds')
            _require(name not in {s['name'] for s in segments}, 'duplicate_segment')
            segment = dict(name=name, addr=addr, vmsize=vmsize, offset=offset,
                           size=count, command=cursor, nsects=nsects,
                           maxprot=maxprot, initprot=initprot, flags=segflags)
            segments.append(segment)
            for index in range(nsects):
                fields = struct.unpack_from('<16s16sQQ8I', raw, 72 + index * 80)
                sectname, segname, address, length, off, align, reloff, nreloc, sectflags, _, _, _ = fields
                _fixed_name(sectname)
                _require(_fixed_name(segname) == name and align <= 31
                         and addr <= address <= address + length <= addr + vmsize,
                         'section_bounds')
                if length:
                    section_vm_ranges.append((address, address + length))
                # These three section types have virtual bytes only.
                if sectflags & 0xFF in (1, 0xC, 0x12):
                    # A zero-fill section has VM extent, not file-backed data.
                    # Two measured arm64 pymupdf thread_bss sections retain a
                    # mapped offset into zero file padding. Accept that exact
                    # form; the field AND those padding bytes remain immutable.
                    _require(off == 0 or ((sectflags & 0xFF) == 0x12
                             and off == offset + address - addr
                             and commands_end <= off <= off + length <= offset + count
                             and not any(data[off:off + length])), 'zerofill_offset')
                    if off and length:
                        zero_file_padding.append((off, off + length))
                elif length:
                    _require(commands_end <= off and offset <= off
                             and off + length <= offset + count
                             and off % (1 << align) == 0
                             and address - addr == off - offset, 'section_bounds')
                    sections.append((off, off + length))
                else:
                    _require(off == 0 or max(commands_end, offset) <= off <= offset + count,
                             'empty_section_offset')
                if nreloc:
                    link_ranges.append((reloff, nreloc * 8))
                else:
                    _require(reloff == 0, 'empty_relocation_offset')
        elif command == LC_CODE_SIGNATURE or command in _LINKEDIT_DATA:
            _require(size == 16, 'command_size')
            off, length = struct.unpack_from('<II', raw, 8)
            if command == LC_CODE_SIGNATURE:
                signature = (cursor, off, length)
            else:
                link_ranges.append((off, length))
        elif command == 2:  # LC_SYMTAB, nlist_64 entries
            _require(size == 24, 'command_size')
            symtab = struct.unpack_from('<4I', raw, 8)
            link_ranges.extend(((symtab[0], symtab[1] * 16), (symtab[2], symtab[3])))
        elif command == 0xB:  # LC_DYSYMTAB
            _require(size == 80, 'command_size')
            dysymtab = struct.unpack_from('<18I', raw, 8)
            for index, width in ((6, 8), (8, 56), (10, 4), (12, 4), (14, 8), (16, 8)):
                link_ranges.append((dysymtab[index], dysymtab[index + 1] * width))
        elif command in (0x22, 0x80000022):  # LC_DYLD_INFO[_ONLY]
            _require(size == 48 and not ({0x22, 0x80000022} - {command}) & seen,
                     'dyld_info')
            fields = struct.unpack_from('<10I', raw, 8)
            link_ranges.extend(zip(fields[::2], fields[1::2]))
        elif command == 0x16:  # LC_TWOLEVEL_HINTS
            _require(size == 16, 'command_size')
            off, length = struct.unpack_from('<II', raw, 8)
            link_ranges.append((off, length * 4))
        elif command in _DYLIB_COMMANDS:
            _inline_string(raw, 24)
        elif command in _STRING_COMMANDS:
            _inline_string(raw, 12)
        elif command in (0x1B, 0x24, 0x2A):  # UUID, VERSION_MIN_MACOSX, SOURCE_VERSION
            _require(size == (24 if command == 0x1B else 16), 'command_size')
        elif command == 0x32:  # LC_BUILD_VERSION, only macOS
            _require(size >= 24, 'command_size')
            platform, _, _, tools = struct.unpack_from('<4I', raw, 8)
            _require(platform == 1 and size == 24 + tools * 8, 'build_version')
        elif command == 0x80000028:  # LC_MAIN
            _require(size == 24 and filetype == 2, 'entry_point')
            entry = struct.unpack_from('<Q', raw, 8)[0]
        else:
            raise ValueError(f'macho_delta_command_unsupported_0x{command:08x}')
        cursor += size
    _require(cursor == commands_end, 'load_commands')
    if signature is None:
        _require(allow_missing and cpu == 0x01000007 and subtype == 3,
                 'signature_addition_unsupported')
        signature_command, sigoff, sigsize = None, len(data), 0
        _require(bool(sections) and commands_end + 16 <= min(start for start, _ in sections)
                 and not any(data[commands_end:commands_end + 16]), 'signature_header_space')
    else:
        signature_command, sigoff, sigsize = signature
        _require(sigoff >= commands_end and sigoff % 16 == 0 and sigsize > 0
                 and sigoff + sigsize == len(data), 'signature_terminal_bounds')
    linkedit = [s for s in segments if s['name'] == b'__LINKEDIT']
    text = [s for s in segments if s['name'] == b'__TEXT']
    _require(len(linkedit) == len(text) == 1, 'required_segments')
    linkedit, text = linkedit[0], text[0]
    _require(linkedit['nsects'] == 0 and linkedit['maxprot'] == linkedit['initprot'] == 1
             and linkedit['offset'] >= commands_end and linkedit['offset'] <= sigoff
             and linkedit['offset'] + linkedit['size'] == len(data), 'linkedit_layout')
    _require(text['offset'] == 0 and text['size'] >= commands_end and text['initprot'] == 5,
             'text_layout')
    _disjoint([(s['offset'], s['offset'] + s['size']) for s in segments if s['size']], 'segment_overlap')
    _disjoint([(s['addr'], s['addr'] + s['vmsize']) for s in segments if s['vmsize']], 'virtual_overlap')
    _disjoint(sections, 'section_overlap')
    _disjoint(section_vm_ranges, 'section_virtual_overlap')
    _disjoint(sections + zero_file_padding, 'zerofill_padding_overlap')
    _require(all(s['addr'] + s['vmsize'] <= linkedit['addr']
                 for s in segments if s is not linkedit), 'linkedit_not_final')
    if entry is not None:
        _require(commands_end <= entry < text['size']
                 and any(start <= entry < stop for start, stop in sections), 'entry_point')
    if dysymtab is not None:
        _require(symtab is not None, 'symbol_indexes')
        for index in (0, 2, 4):
            _require(dysymtab[index] + dysymtab[index + 1] <= symtab[1], 'symbol_indexes')
    occupied = []
    for offset, length in link_ranges:
        if length:
            _require(linkedit['offset'] <= offset < offset + length <= sigoff,
                     'linkedit_reference_bounds')
            occupied.append((offset, offset + length))
        else:
            _require(offset == 0 or linkedit['offset'] <= offset <= sigoff,
                     'empty_linkedit_reference')
    _disjoint(occupied, 'linkedit_reference_overlap')
    if signature is not None:
        try:
            _signature_frame(data[sigoff:])
        except ValueError as exc:
            raise ValueError(f'{exc}; signature_offset={sigoff}, '
                             f'signature_datasize={sigsize}, cpu=0x{cpu:x}') from exc
    return _Layout(cpu, subtype, ncmds, commands_end, signature_command, sigoff, sigsize, linkedit['command'],
                   linkedit['offset'], linkedit['size'], linkedit['vmsize'])


def _verify_thin(original, signed):
    before = _layout(original, allow_missing=True)
    after = _layout(signed)
    _require((before.cpu, before.subtype, before.linkedit_command, before.linkedit_offset) ==
             (after.cpu, after.subtype, after.linkedit_command, after.linkedit_offset), 'layout_changed')
    mutable_fields = [(before.linkedit_command + 32, 8),
                      (before.linkedit_command + 48, 8)]
    if before.signature_command is None:
        _require(after.command_count == before.command_count + 1
                 and after.commands_end == before.commands_end + 16
                 and after.signature_command == before.commands_end,
                 'signature_addition_commands')
        expected_offset = (len(original) + 15) // 16 * 16
        _require(after.signature_offset == expected_offset
                 and not any(signed[len(original):expected_offset]), 'signature_addition_padding')
        mutable_fields += [(16, 8), (before.commands_end, 16)]
        added_padding = expected_offset - len(original)
        evidence = 'STRUCTURAL_SIGNATURE_ADDITION_ONLY'
    else:
        _require(before.signature_command == after.signature_command
                 and before.signature_offset == after.signature_offset, 'layout_changed')
        mutable_fields.append((before.signature_command + 12, 4))
        added_padding = 0
        evidence = 'STRUCTURAL_SIGNATURE_REPLACEMENT_ONLY'
    _require(after.linkedit_size - before.linkedit_size ==
             after.signature_size - before.signature_size + added_padding, 'linkedit_resize')
    _require(after.signature_size % 16 == 0, 'signature_allocation_alignment')
    expected_vm_size = (after.linkedit_size + 16383) // 16384 * 16384
    _require(after.linkedit_vm_size == expected_vm_size, 'linkedit_vm_resize')
    # Masks originate in the ORIGINAL parser. Each mutable value is independently
    # constrained above, including the exact new command and terminal padding.
    cursor = 0
    preserved = hashlib.sha256()
    for offset, length in sorted(mutable_fields):
        _require(original[cursor:offset] == signed[cursor:offset], 'payload_changed')
        preserved.update(original[cursor:offset])
        cursor = offset + length
    _require(original[cursor:before.signature_offset] == signed[cursor:before.signature_offset],
             'payload_changed')
    preserved.update(original[cursor:before.signature_offset])
    return {
        'format': 'jae-macho-signature-delta-v1',
        'evidence': evidence,
        'allocator_contract': ALLOCATOR_CONTRACT,
        'architecture': 'arm64' if before.cpu == 0x0100000C else 'x86_64',
        'original_sha256': hashlib.sha256(original).hexdigest(),
        'signed_sha256': hashlib.sha256(signed).hexdigest(),
        'signature_offset': after.signature_offset,
        'original_signature_size': before.signature_size,
        'signed_signature_size': after.signature_size,
        'preserved_ranges_sha256': preserved.hexdigest(),
        'mutable_load_command_fields': [{'offset': offset, 'size': size}
                                        for offset, size in sorted(mutable_fields)],
        'signature_validation': 'NOT_PERFORMED',
        'publisher_validation': 'NOT_PERFORMED',
        'notarization': 'NOT_PERFORMED',
        'consumer_admission': 'DISALLOWED',
    }


def _fat_slices(data, *, output=False):
    """Only measured FAT_MAGIC, two minimally packed x86_64/arm64 slices."""
    _require(type(data) is bytes, 'bytes_required')
    _require(48 <= len(data) <= MAX_IMAGE_BYTES, 'image_size')
    _require(data[:4] == b'\xca\xfe\xba\xbe'
             and struct.unpack_from('>I', data, 4)[0] == 2, 'fat_unsupported')
    slices, end = [], 48
    for index, expected in enumerate(((0x01000007, 3), (0x0100000C, 0))):
        cpu, subtype, offset, size, align = struct.unpack_from('>5I', data, 8 + index * 20)
        _require((cpu, subtype) == expected
                 and align in ((12, 13, 14) if index == 0 and not output else (14,)),
                 'fat_arch_unsupported')
        expected_offset = (end + (1 << align) - 1) // (1 << align) * (1 << align)
        _require(offset == expected_offset and 32 <= size
                 and offset + size <= len(data), 'fat_slice_bounds')
        _require(not any(data[end:offset]), 'fat_nonzero_padding')
        thin = data[offset:offset + size]
        _require(thin[:4] == b'\xcf\xfa\xed\xfe'
                 and struct.unpack_from('<II', thin, 4) == (cpu, subtype), 'fat_slice_identity')
        slices.append((cpu, subtype, offset, size, align, thin))
        end = offset + size
    _require(end == len(data), 'fat_trailing_bytes')
    return slices


def inspect_signing_input(original: bytes) -> dict:
    """Read-only ORIGINAL-layout coverage, including unsigned Intel FAT slices.

    This does not simulate signing, fabricate post-sign bytes, or call the delta
    successful. Its result is not evidence that a platform signing transition
    conforms to the allowed subset; actual post-sign bytes are still required.
    """
    _require(type(original) is bytes, 'bytes_required')
    if original[:4] in _FAT_MAGIC:
        slices = _fat_slices(original)
    else:
        slices = [(None, None, 0, len(original), None, original)]
    coverage = []
    for cpu, subtype, offset, size, align, data in slices:
        layout = _layout(data, allow_missing=True)
        coverage.append({'architecture': 'arm64' if layout.cpu == 0x0100000C else 'x86_64',
                         'subtype': layout.subtype, 'offset': offset, 'bytes': size,
                         'fat_alignment_power': align,
                         'signature_action': 'add' if layout.signature_command is None else 'replace'})
    return {'format': 'jae-macho-input-layout-v1', 'evidence': 'ORIGINAL_LAYOUT_ONLY',
            'original_sha256': hashlib.sha256(original).hexdigest(), 'slices': coverage,
            'signature_validation': 'NOT_PERFORMED', 'publisher_validation': 'NOT_PERFORMED',
            'notarization': 'NOT_PERFORMED', 'consumer_admission': 'DISALLOWED'}


def verify_signature_only_change(original: bytes, signed: bytes) -> dict:
    """Return structural preservation evidence or raise ValueError, fail closed.

    The caller authenticates ORIGINAL provenance separately. Every prospective
    signed slice must contain a framed signature. No result here authenticates
    any signature, signer, notarization, build origin or consumer eligibility.
    """
    _require(type(original) is bytes and type(signed) is bytes, 'bytes_required')
    original_fat, signed_fat = original[:4] in _FAT_MAGIC, signed[:4] in _FAT_MAGIC
    _require(original_fat == signed_fat, 'container_changed')
    if not original_fat:
        return _verify_thin(original, signed)
    before, after = _fat_slices(original), _fat_slices(signed, output=True)
    transitions = []
    for old, new in zip(before, after):
        _require((old[0], old[1]) == (new[0], new[1]), 'fat_arch_changed')
        try:
            transition = _verify_thin(old[5], new[5])
        except ValueError as exc:
            raise ValueError(f'{exc}; fat_cpu=0x{old[0]:x}, '
                             f'original_slice_offset={old[2]}, signed_slice_offset={new[2]}') from exc
        transitions.append({'original_offset': old[2], 'signed_offset': new[2],
                            'original_fat_alignment_power': old[4],
                            'signed_fat_alignment_power': new[4],
                            'transition': transition})
    return {'format': 'jae-macho-signature-delta-v1',
            'evidence': 'STRUCTURAL_FAT_SIGNATURE_CHANGES_ONLY',
            'allocator_contract': ALLOCATOR_CONTRACT,
            'architecture': 'x86_64+arm64',
            'original_sha256': hashlib.sha256(original).hexdigest(),
            'signed_sha256': hashlib.sha256(signed).hexdigest(),
            'slices': transitions, 'signature_validation': 'NOT_PERFORMED',
            'publisher_validation': 'NOT_PERFORMED', 'notarization': 'NOT_PERFORMED',
            'consumer_admission': 'DISALLOWED'}
