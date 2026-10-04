"""Clearly synthetic Mach-O framing tests; no signing, platform or publisher PASS.

The blobs deliberately are not executable Mach-O code or valid CodeDirectories.
They only exercise the documented structural/byte-preservation subset.
"""
from __future__ import annotations

import hashlib
import json
import struct
import subprocess

import pytest

from scripts import macos_signing_delta as delta


TEXT_OFFSET = 0x400
DATA_OFFSET = 0x1000
LINKEDIT_OFFSET = 0x2000
SIGNATURE_OFFSET = 0x2080


def synthetic_signature(payload=b'SYNTHETIC CMS, NOT A SIGNATURE', *, allocation=128):
    directory = struct.pack('>II', 0xFADE0C02, 44) + b'\0' * 36
    cms = struct.pack('>II', 0xFADE0B01, 8 + len(payload)) + payload
    length = 28 + len(directory) + len(cms)
    assert allocation >= length
    return (struct.pack('>III4I', 0xFADE0CC0, length, 2, 0, 28, 0x10000, 72)
            + directory + cms).ljust(allocation, b'\0')


def synthetic_macho(payload=b'SYNTHETIC OLD', *, signature_size=128,
                    linkedit_vm_size=16384, cpu=0x0100000C, extra_commands=()):
    """Static, non-executable bytes; reusable only for developer-boundary tests."""
    base = 0x100000000

    def segment(name, offset, size, vm_size, protection, section=None):
        raw = struct.pack('<II16sQQQQiiII', delta.LC_SEGMENT_64,
                          72 + (80 if section else 0), name,
                          base + offset, vm_size, offset, size,
                          protection, protection, int(section is not None), 0)
        if section:
            sectname, section_offset, section_size = section
            raw += struct.pack('<16s16sQQ8I', sectname, name, base + section_offset,
                               section_size, section_offset, 4, 0, 0, 0, 0, 0, 0)
        return raw

    linkedit_size = SIGNATURE_OFFSET - LINKEDIT_OFFSET + signature_size
    commands = [
        segment(b'__TEXT', 0, 4096, 4096, 5, (b'__text', TEXT_OFFSET, 64)),
        segment(b'__DATA', 4096, 4096, 4096, 3, (b'__data', DATA_OFFSET, 32)),
        segment(b'__LINKEDIT', LINKEDIT_OFFSET, linkedit_size, linkedit_vm_size, 1),
        struct.pack('<6I', 2, 24, LINKEDIT_OFFSET, 2, LINKEDIT_OFFSET + 32, 32),
        struct.pack('<20I', 0xB, 80, 0, 1, 1, 1, 2, 0, 0, 0, 0, 0, 0, 0,
                    LINKEDIT_OFFSET + 80, 2, 0, 0, 0, 0),
        struct.pack('<4I', 0x26, 16, LINKEDIT_OFFSET + 64, 16),
        struct.pack('<12I', 0x80000022, 48, *([0] * 10)),
        struct.pack('<II16s', 0x1B, 24, b'SYNTHETIC-UUID!!'),
        struct.pack('<6I', 0x32, 24, 1, 0x000D0000, 0x000D0000, 0),
        struct.pack('<IIQQ', 0x80000028, 24, TEXT_OFFSET, 0),
        *extra_commands,
        struct.pack('<4I', delta.LC_CODE_SIGNATURE, 16, SIGNATURE_OFFSET, signature_size),
    ]
    header = struct.pack('<8I', 0xFEEDFACF, cpu, 0 if cpu == 0x0100000C else 3,
                         2, len(commands), sum(map(len, commands)), 0, 0)
    prefix = bytearray((header + b''.join(commands)).ljust(SIGNATURE_OFFSET, b'\0'))
    prefix[TEXT_OFFSET:TEXT_OFFSET + 64] = b'NONEXECUTABLE SYNTHETIC CODE'.ljust(64, b'T')
    prefix[DATA_OFFSET:DATA_OFFSET + 32] = b'SYNTHETIC IMMUTABLE DATA'.ljust(32, b'D')
    prefix[LINKEDIT_OFFSET:LINKEDIT_OFFSET + 88] = bytes(range(88))
    return bytes(prefix) + synthetic_signature(payload, allocation=signature_size)


def command_offset(data, command, *, occurrence=0):
    offset = 32
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        cmd, size = struct.unpack_from('<II', data, offset)
        if cmd == command:
            if occurrence == 0:
                return offset
            occurrence -= 1
        offset += size
    raise AssertionError(f'no command {command}')


def change(data, offset, value, fmt='<I'):
    result = bytearray(data)
    struct.pack_into(fmt, result, offset, value)
    return bytes(result)


def test_signature_replacement_is_evidence_only_and_runs_no_tools(monkeypatch):
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('no subprocess'))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('no subprocess'))
    original = synthetic_macho()
    signed = synthetic_macho(b'SYNTHETIC NEW', signature_size=160)
    evidence = delta.verify_signature_only_change(original, signed)
    assert evidence['format'] == 'jae-macho-signature-delta-v1'
    assert evidence['evidence'] == 'STRUCTURAL_SIGNATURE_REPLACEMENT_ONLY'
    assert evidence['signature_validation'] == evidence['publisher_validation'] == 'NOT_PERFORMED'
    assert evidence['notarization'] == 'NOT_PERFORMED'
    assert evidence['consumer_admission'] == 'DISALLOWED'
    assert evidence['original_sha256'] == hashlib.sha256(original).hexdigest()
    assert evidence['signed_sha256'] == hashlib.sha256(signed).hexdigest()
    assert evidence['signature_offset'] == SIGNATURE_OFFSET
    assert evidence['original_signature_size'] == 128
    assert evidence['signed_signature_size'] == 160
    assert json.loads(json.dumps(evidence)) == evidence


@pytest.mark.parametrize('cpu,page', [(0x0100000C, 16384), (0x01000007, 16384)])
@pytest.mark.parametrize('new_size', [128, 160, 20000])
def test_same_size_growth_and_necessary_mapping_growth(cpu, page, new_size):
    vm_size = ((128 + new_size + page - 1) // page) * page
    result = delta.verify_signature_only_change(synthetic_macho(cpu=cpu),
        synthetic_macho(b'SYNTHETIC NEW', cpu=cpu, signature_size=new_size, linkedit_vm_size=vm_size))
    assert result['architecture'] == ('arm64' if cpu == 0x0100000C else 'x86_64')


def test_signature_shrink_preserves_original_mapping_and_non_signature_bytes():
    original = synthetic_macho(signature_size=5000, linkedit_vm_size=16384)
    signed = synthetic_macho(b'SYNTHETIC NEW', signature_size=128, linkedit_vm_size=16384)
    result = delta.verify_signature_only_change(original, signed)
    assert result['signed_signature_size'] == 128


def test_identity_is_only_structural_evidence_never_signature_success():
    original = synthetic_macho()
    assert delta.verify_signature_only_change(original, original)['signature_validation'] == 'NOT_PERFORMED'


@pytest.mark.parametrize('offset', [4, 8, 12, 16, 20, 24, 28, 33, 39, 60, 84, 100,
                                    104, 160, 189, TEXT_OFFSET, TEXT_OFFSET + 63,
                                    2000, DATA_OFFSET, LINKEDIT_OFFSET,
                                    LINKEDIT_OFFSET + 87, SIGNATURE_OFFSET - 1])
def test_any_other_code_data_header_command_or_padding_change_is_rejected(offset):
    original = synthetic_macho()
    signed = bytearray(synthetic_macho(b'SYNTHETIC NEW'))
    signed[offset] ^= 1
    with pytest.raises(ValueError, match='macho_delta_'):
        delta.verify_signature_only_change(original, bytes(signed))


@pytest.mark.parametrize('vm_size', [4095, 4096, 8192, 32768])
def test_unnecessary_mapping_changes_are_rejected(vm_size):
    with pytest.raises(ValueError, match='macho_delta_linkedit_vm_resize'):
        delta.verify_signature_only_change(synthetic_macho(),
            synthetic_macho(b'SYNTHETIC NEW', linkedit_vm_size=vm_size))


def test_incorrect_growth_rounding_is_rejected():
    with pytest.raises(ValueError, match='macho_delta_linkedit_vm_resize'):
        delta.verify_signature_only_change(synthetic_macho(),
            synthetic_macho(b'SYNTHETIC NEW', signature_size=5008, linkedit_vm_size=8192))


@pytest.mark.parametrize('magic', [b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca',
                                   b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'])
def test_fat_is_explicitly_unsupported(magic):
    raw = magic + synthetic_macho()[4:]
    with pytest.raises(ValueError, match='macho_delta_fat_unsupported'):
        delta.verify_signature_only_change(raw, raw)


@pytest.mark.parametrize('magic', [b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'ELF\0'])
def test_other_formats_are_explicitly_unsupported(magic):
    raw = magic + synthetic_macho()[4:]
    with pytest.raises(ValueError, match='macho_delta_format_unsupported'):
        delta.verify_signature_only_change(raw, raw)


def test_missing_signature_command_is_not_inferred_from_magic_or_trailing_blob():
    raw = synthetic_macho()
    pos = command_offset(raw, delta.LC_CODE_SIGNATURE)
    raw = change(change(raw, 16, 10), 20, pos - 32)
    with pytest.raises(ValueError, match='macho_delta_signature_addition_unsupported'):
        delta.verify_signature_only_change(raw, synthetic_macho())


@pytest.mark.parametrize('cmd', [0x999, 0x31, 0x80000035, 0x2C, 5])
def test_unknown_note_fileset_encryption_and_thread_commands_are_unsupported(cmd):
    raw = synthetic_macho(extra_commands=[struct.pack('<4I', cmd, 16, 0, 0)])
    with pytest.raises(ValueError, match='macho_delta_command_unsupported'):
        delta.verify_signature_only_change(raw, raw)


def test_extra_valid_command_is_still_not_an_allowed_delta():
    raw = synthetic_macho(extra_commands=[struct.pack('<4I', 0x24, 16, 0x000D0000, 0)])
    with pytest.raises(ValueError, match='macho_delta_'):
        delta.verify_signature_only_change(synthetic_macho(), raw)


@pytest.mark.parametrize('command', [delta.LC_CODE_SIGNATURE, 0x1B, 2])
def test_duplicate_singleton_commands_are_refused(command):
    raw = synthetic_macho()
    pos = command_offset(raw, command)
    size = struct.unpack_from('<I', raw, pos + 4)[0]
    raw = synthetic_macho(extra_commands=[raw[pos:pos + size]])
    with pytest.raises(ValueError, match='macho_delta_duplicate_command'):
        delta.verify_signature_only_change(raw, raw)


@pytest.mark.parametrize('damage', ['truncated', 'trailing_zero', 'trailing_text', 'sig_offset',
                                    'sig_size', 'sig_unaligned', 'linkedit_end', 'linkedit_offset',
                                    'linkedit_vm_overlap', 'linkedit_executable', 'segment_overlap',
                                    'section_overlap', 'section_in_header', 'section_outside_segment',
                                    'section_vm_mismatch', 'reloc_signature', 'sym_signature',
                                    'strings_signature', 'dyld_signature', 'functions_signature',
                                    'indirect_signature', 'overlapping_linkedit', 'symbol_index',
                                    'invalid_command_size', 'invalid_ncmds', 'invalid_sizeofcmds'])
def test_malformed_ranges_and_ambiguous_layouts_are_rejected_even_if_unchanged(damage):
    raw = synthetic_macho()
    text = command_offset(raw, delta.LC_SEGMENT_64)
    data = command_offset(raw, delta.LC_SEGMENT_64, occurrence=1)
    linkedit = command_offset(raw, delta.LC_SEGMENT_64, occurrence=2)
    signature = command_offset(raw, delta.LC_CODE_SIGNATURE)
    symtab = command_offset(raw, 2)
    dysymtab = command_offset(raw, 0xB)
    if damage == 'truncated': raw = raw[:-1]
    elif damage == 'trailing_zero': raw += b'\0'
    elif damage == 'trailing_text': raw += b'ARBITRARY APPENDED DATA'
    elif damage == 'sig_offset': raw = change(raw, signature + 8, SIGNATURE_OFFSET - 16)
    elif damage == 'sig_size': raw = change(raw, signature + 12, 127)
    elif damage == 'sig_unaligned':
        raw = change(change(raw, signature + 8, SIGNATURE_OFFSET - 1), signature + 12, 129)
    elif damage == 'linkedit_end': raw = change(raw, linkedit + 48, 255, '<Q')
    elif damage == 'linkedit_offset': raw = change(raw, linkedit + 40, SIGNATURE_OFFSET + 16, '<Q')
    elif damage == 'linkedit_vm_overlap': raw = change(raw, linkedit + 24, 0x100001000, '<Q')
    elif damage == 'linkedit_executable':
        raw = change(change(raw, linkedit + 56, 5), linkedit + 60, 5)
    elif damage == 'segment_overlap': raw = change(raw, data + 40, 0, '<Q')
    elif damage == 'section_overlap':
        raw = change(change(raw, data + 40, 0, '<Q'), data + 72 + 48, TEXT_OFFSET)
    elif damage == 'section_in_header': raw = change(raw, text + 72 + 48, 32)
    elif damage == 'section_outside_segment': raw = change(raw, text + 72 + 48, SIGNATURE_OFFSET)
    elif damage == 'section_vm_mismatch': raw = change(raw, text + 72 + 32, 0x100000800, '<Q')
    elif damage == 'reloc_signature':
        raw = change(change(raw, text + 72 + 56, SIGNATURE_OFFSET), text + 72 + 60, 1)
    elif damage == 'sym_signature': raw = change(raw, symtab + 8, SIGNATURE_OFFSET)
    elif damage == 'strings_signature': raw = change(raw, symtab + 16, SIGNATURE_OFFSET)
    elif damage == 'dyld_signature':
        pos = command_offset(raw, 0x80000022)
        raw = change(change(raw, pos + 8, SIGNATURE_OFFSET), pos + 12, 1)
    elif damage == 'functions_signature': raw = change(raw, command_offset(raw, 0x26) + 8, SIGNATURE_OFFSET)
    elif damage == 'indirect_signature': raw = change(raw, dysymtab + 8 + 12 * 4, SIGNATURE_OFFSET)
    elif damage == 'overlapping_linkedit': raw = change(raw, symtab + 16, LINKEDIT_OFFSET + 16)
    elif damage == 'symbol_index': raw = change(raw, dysymtab + 8, 3)
    elif damage == 'invalid_command_size': raw = change(raw, text + 4, 73)
    elif damage == 'invalid_ncmds': raw = change(raw, 16, 0xFFFFFFFF)
    elif damage == 'invalid_sizeofcmds': raw = change(raw, 20, 0xFFFFFFF8)
    else: raise AssertionError(damage)
    with pytest.raises(ValueError, match='macho_delta_'):
        delta.verify_signature_only_change(raw, raw)


@pytest.mark.parametrize('offset,value', [(0, 0), (4, 129), (8, 129), (12, 1),
                                          (16, 12), (20, 0), (24, 28),
                                          (28, 0), (32, 43), (76, 1000), (127, 1)])
def test_signature_component_framing_rejects_malformed_or_unframed_bytes(offset, value):
    raw = synthetic_macho()
    if offset == 127:
        raw = raw[:-1] + b'X'
    else:
        raw = change(raw, SIGNATURE_OFFSET + offset, value, '>I')
    with pytest.raises(ValueError, match='macho_delta_signature_'):
        delta.verify_signature_only_change(raw, raw)


@pytest.mark.parametrize('raw', [b'', b'x' * 31, bytearray(synthetic_macho()), memoryview(synthetic_macho())])
def test_only_bounded_immutable_bytes_are_accepted(raw):
    with pytest.raises(ValueError, match='macho_delta_(image_size|bytes_required)'):
        delta.verify_signature_only_change(raw, raw)


def test_resource_bounds_are_enforced_before_parsing(monkeypatch):
    monkeypatch.setattr(delta, 'MAX_IMAGE_BYTES', 100)
    with pytest.raises(ValueError, match='macho_delta_image_size'):
        delta.verify_signature_only_change(synthetic_macho(), synthetic_macho())


def test_whole_preserved_byte_region_is_checked_exhaustively():
    """Every single-byte mutation outside the three narrow fields must fail."""
    original = synthetic_macho()
    baseline = synthetic_macho(b'SYNTHETIC NEW')
    evidence = delta.verify_signature_only_change(original, baseline)
    exempt = {i for field in evidence['mutable_load_command_fields']
              for i in range(field['offset'], field['offset'] + field['size'])}
    for offset in range(SIGNATURE_OFFSET):
        if offset not in exempt:
            damaged = bytearray(baseline)
            damaged[offset] ^= 1
            with pytest.raises(ValueError, match='macho_delta_'):
                delta.verify_signature_only_change(original, bytes(damaged))


@pytest.mark.parametrize('shift', [-16, 16])
def test_signature_relocation_cannot_swallow_or_insert_original_payload(shift):
    original = synthetic_macho()
    signed = synthetic_macho(b'SYNTHETIC NEW')
    if shift < 0:
        signed = signed[:SIGNATURE_OFFSET + shift] + signed[SIGNATURE_OFFSET:]
    else:
        signed = signed[:SIGNATURE_OFFSET] + b'\0' * shift + signed[SIGNATURE_OFFSET:]
    signed = change(signed, command_offset(signed, delta.LC_CODE_SIGNATURE) + 8,
                    SIGNATURE_OFFSET + shift)
    signed = change(signed, command_offset(signed, delta.LC_SEGMENT_64, occurrence=2) + 48,
                    len(signed) - LINKEDIT_OFFSET, '<Q')
    # The prospective image is well framed by itself; its different dataoff
    # nevertheless cannot redefine which ORIGINAL bytes are mutable.
    delta.verify_signature_only_change(signed, signed)
    with pytest.raises(ValueError, match='macho_delta_layout_changed'):
        delta.verify_signature_only_change(original, signed)


def test_linkedit_size_cannot_grow_independently_of_the_signature():
    original = synthetic_macho()
    signed = change(synthetic_macho(b'SYNTHETIC NEW'),
                    command_offset(original, delta.LC_SEGMENT_64, occurrence=2) + 48,
                    272, '<Q')
    with pytest.raises(ValueError, match='macho_delta_'):
        delta.verify_signature_only_change(original, signed)


@pytest.mark.parametrize('field,value', [(48, 0xFFFFFFFF), (56, 0xFFFFFFFF), (60, 1)])
def test_malformed_empty_section_and_relocation_offsets_are_rejected(field, value):
    original = synthetic_macho()
    section = command_offset(original, delta.LC_SEGMENT_64) + 72
    original = change(original, section + 40, 0, '<Q')
    original = change(original, section + field, value)
    with pytest.raises(ValueError, match='macho_delta_'):
        delta.verify_signature_only_change(original, original)


def test_duplicate_dylib_identity_command_is_ambiguous():
    command = struct.pack('<6I', 0xD, 32, 24, 0, 0, 0) + b'lib\0\0\0\0\0'
    original = synthetic_macho(extra_commands=[command, command])
    with pytest.raises(ValueError, match='macho_delta_duplicate_command'):
        delta.verify_signature_only_change(original, original)


def test_signature_allocation_bound_is_separate_from_image_bound(monkeypatch):
    monkeypatch.setattr(delta, 'MAX_SIGNATURE_BYTES', 127)
    with pytest.raises(ValueError, match='macho_delta_signature_size'):
        delta.verify_signature_only_change(synthetic_macho(), synthetic_macho())


def test_original_layout_must_be_valid_even_when_prospective_layout_is_valid():
    original = change(synthetic_macho(), SIGNATURE_OFFSET, 0, '>I')
    with pytest.raises(ValueError, match='macho_delta_signature_frame'):
        delta.verify_signature_only_change(original, synthetic_macho(b'SYNTHETIC NEW'))


def synthetic_unsigned_macho(*, trim=0):
    """Remove the synthetic fixture's final LC/blob, leaving zero header room."""
    raw = synthetic_macho(cpu=0x01000007)
    pos = command_offset(raw, delta.LC_CODE_SIGNATURE)
    raw = bytearray(raw[:SIGNATURE_OFFSET - trim])
    raw[pos:pos + 16] = b'\0' * 16
    count, size = struct.unpack_from('<II', raw, 16)
    struct.pack_into('<II', raw, 16, count - 1, size - 16)
    struct.pack_into('<Q', raw, command_offset(raw, delta.LC_SEGMENT_64, occurrence=2) + 48,
                     len(raw) - LINKEDIT_OFFSET)
    return bytes(raw)


def synthetic_added_signature(original, *, allocation=160):
    """Model bytes for tests only, never a real-file signing or allocator tool."""
    assert original[4:8] == struct.pack('<I', 0x01000007)
    raw = bytearray(original)
    count, cmdsize = struct.unpack_from('<II', raw, 16)
    sigoff = (len(raw) + 15) // 16 * 16
    raw.extend(b'\0' * (sigoff - len(raw)))
    raw.extend(synthetic_signature(b'SYNTHETIC ADDITION', allocation=allocation))
    struct.pack_into('<4I', raw, 32 + cmdsize, delta.LC_CODE_SIGNATURE, 16, sigoff, allocation)
    struct.pack_into('<II', raw, 16, count + 1, cmdsize + 16)
    linkedit = command_offset(raw, delta.LC_SEGMENT_64, occurrence=2)
    filesize = len(raw) - LINKEDIT_OFFSET
    struct.pack_into('<Q', raw, linkedit + 48, filesize)
    struct.pack_into('<Q', raw, linkedit + 32, (filesize + 16383) // 16384 * 16384)
    return bytes(raw)


def synthetic_fat(intel, arm, *, intel_alignment=12, arm_alignment=14):
    """Only the observed, ordered two-architecture FAT32 envelope."""
    payload = bytearray(struct.pack('>II', 0xCAFEBABE, 2) + b'\0' * 40)
    for index, (data, cpu, subtype, alignment) in enumerate((
            (intel, 0x01000007, 3, intel_alignment),
            (arm, 0x0100000C, 0, arm_alignment))):
        offset = (len(payload) + (1 << alignment) - 1) // (1 << alignment) * (1 << alignment)
        payload.extend(b'\0' * (offset - len(payload)))
        payload.extend(data)
        struct.pack_into('>5I', payload, 8 + index * 20, cpu, subtype, offset, len(data), alignment)
    return bytes(payload)


@pytest.mark.parametrize('trim', [0, 1, 7, 15])
def test_unsigned_intel_command_addition_preserves_all_original_payload(trim):
    original = synthetic_unsigned_macho(trim=trim)
    signed = synthetic_added_signature(original)
    inspection = delta.inspect_signing_input(original)
    assert inspection['evidence'] == 'ORIGINAL_LAYOUT_ONLY'
    assert inspection['slices'][0]['signature_action'] == 'add'
    result = delta.verify_signature_only_change(original, signed)
    assert result['evidence'] == 'STRUCTURAL_SIGNATURE_ADDITION_ONLY'
    assert result['original_signature_size'] == 0
    assert result['signature_offset'] - len(original) == trim
    assert result['allocator_contract'] == delta.ALLOCATOR_CONTRACT


def test_original_unsigned_layout_does_not_count_as_signed_output():
    original = synthetic_unsigned_macho()
    delta.inspect_signing_input(original)
    with pytest.raises(ValueError, match='macho_delta_signature_addition_unsupported'):
        delta.verify_signature_only_change(original, original)


@pytest.mark.parametrize('damage', ['nonzero_header', 'insufficient_room', 'wrong_command',
                                    'wrong_ncmds', 'wrong_sizeofcmds', 'nonzero_alignment',
                                    'extra_alignment', 'code', 'data', 'padding', 'metadata'])
def test_addition_refuses_every_unapproved_change(damage):
    original = synthetic_unsigned_macho(trim=7)
    signed = synthetic_added_signature(original)
    command_end = 32 + struct.unpack_from('<I', original, 20)[0]
    if damage == 'nonzero_header': original = change(original, command_end, 1)
    elif damage == 'insufficient_room':
        section = command_offset(original, delta.LC_SEGMENT_64) + 72
        original = change(change(original, section + 48, command_end + 8),
                          section + 32, 0x100000000 + command_end + 8, '<Q')
    elif damage == 'wrong_command': signed = change(signed, command_end, 0x26)
    elif damage == 'wrong_ncmds': signed = change(signed, 16, 10)
    elif damage == 'wrong_sizeofcmds': signed = change(signed, 20, command_end - 32)
    elif damage == 'nonzero_alignment': signed = signed[:len(original)] + b'X' + signed[len(original) + 1:]
    elif damage == 'extra_alignment':
        signed = signed[:SIGNATURE_OFFSET] + b'\0' * 16 + signed[SIGNATURE_OFFSET:]
        signed = change(signed, command_end + 8, SIGNATURE_OFFSET + 16)
        signed = change(signed, command_offset(signed, delta.LC_SEGMENT_64, occurrence=2) + 48,
                        len(signed) - LINKEDIT_OFFSET, '<Q')
    elif damage == 'code': signed = change(signed, TEXT_OFFSET, 123)
    elif damage == 'data': signed = change(signed, DATA_OFFSET, 123)
    elif damage == 'padding': signed = change(signed, command_end + 16, 123)
    elif damage == 'metadata': signed = change(signed, command_offset(signed, 0x1B) + 8, 123)
    with pytest.raises(ValueError, match='macho_delta_'):
        delta.verify_signature_only_change(original, signed)


@pytest.mark.parametrize('intel_alignment', [12, 13, 14])
@pytest.mark.parametrize('signature_size', [160, 20000])
def test_observed_fat_input_is_preserved_per_slice_with_exact_modern_repacking(intel_alignment, signature_size):
    intel = synthetic_unsigned_macho(trim=7)
    arm = synthetic_macho()
    original = synthetic_fat(intel, arm, intel_alignment=intel_alignment)
    signed = synthetic_fat(synthetic_added_signature(intel, allocation=signature_size),
        synthetic_macho(b'SYNTHETIC ARM NEW', signature_size=signature_size,
                        linkedit_vm_size=(128 + signature_size + 16383) // 16384 * 16384),
        intel_alignment=14)
    coverage = delta.inspect_signing_input(original)
    assert [item['signature_action'] for item in coverage['slices']] == ['add', 'replace']
    result = delta.verify_signature_only_change(original, signed)
    assert result['evidence'] == 'STRUCTURAL_FAT_SIGNATURE_CHANGES_ONLY'
    assert result['slices'][0]['original_fat_alignment_power'] == intel_alignment
    assert result['slices'][0]['signed_fat_alignment_power'] == 14
    assert result['slices'][0]['signed_offset'] == 16384
    assert json.loads(json.dumps(result)) == result


@pytest.mark.parametrize('damage', ['delete_arch', 'duplicate_arch', 'reorder_arch', 'changed_subtype',
                                    'header_padding', 'interslice_padding', 'trailing_zero', 'trailing_data',
                                    'overlap', 'unaligned', 'slice_truncated', 'extra_zero_gap',
                                    'arch_mismatch', 'intel_code', 'arm_data'])
def test_fat_envelope_and_each_architecture_fail_closed(damage):
    intel = synthetic_unsigned_macho()
    original = synthetic_fat(intel, synthetic_macho())
    signed = synthetic_fat(synthetic_added_signature(intel), synthetic_macho(b'SYNTHETIC NEW'),
                           intel_alignment=14)
    first_offset, first_size = struct.unpack_from('>II', signed, 16)
    second_offset, second_size = struct.unpack_from('>II', signed, 36)
    if damage == 'delete_arch': signed = change(signed, 4, 1, '>I')
    elif damage == 'duplicate_arch': signed = change(signed, 28, 0x01000007, '>I')
    elif damage == 'reorder_arch': signed = signed[:8] + signed[28:48] + signed[8:28] + signed[48:]
    elif damage == 'changed_subtype': signed = change(signed, 12, 8, '>I')
    elif damage == 'header_padding': signed = change(signed, 48, 1)
    elif damage == 'interslice_padding': signed = change(signed, first_offset + first_size, 1)
    elif damage == 'trailing_zero': signed += b'\0'
    elif damage == 'trailing_data': signed += b'EXTRA PAYLOAD'
    elif damage == 'overlap': signed = change(signed, 36, first_offset, '>I')
    elif damage == 'unaligned': signed = change(signed, 16, first_offset + 1, '>I')
    elif damage == 'slice_truncated': signed = change(signed, 40, second_size - 1, '>I')
    elif damage == 'extra_zero_gap':
        signed = signed[:second_offset] + b'\0' * 16384 + signed[second_offset:]
        signed = change(signed, 36, second_offset + 16384, '>I')
    elif damage == 'arch_mismatch': signed = change(signed, second_offset + 4, 0x01000007)
    elif damage == 'intel_code': signed = change(signed, first_offset + TEXT_OFFSET, 123)
    elif damage == 'arm_data': signed = change(signed, second_offset + DATA_OFFSET, 123)
    with pytest.raises(ValueError, match='macho_delta_'):
        delta.verify_signature_only_change(original, signed)


@pytest.mark.parametrize('align', [0, 11, 12, 13, 15, 31, 0xFFFFFFFF])
def test_signed_fat_alignment_is_exactly_14(align):
    intel = synthetic_unsigned_macho()
    original = synthetic_fat(intel, synthetic_macho())
    signed = synthetic_fat(synthetic_added_signature(intel), synthetic_macho(b'SYNTHETIC NEW'), intel_alignment=14)
    signed = change(signed, 24, align, '>I')
    with pytest.raises(ValueError, match='macho_delta_fat_arch_unsupported'):
        delta.verify_signature_only_change(original, signed)


def test_modern_vm_recalculation_can_shrink_but_is_not_an_arbitrary_exception():
    original = synthetic_macho(signature_size=20000, linkedit_vm_size=32768)
    signed = synthetic_macho(b'SYNTHETIC SMALL', signature_size=128, linkedit_vm_size=16384)
    delta.verify_signature_only_change(original, signed)
    with pytest.raises(ValueError, match='macho_delta_linkedit_vm_resize'):
        delta.verify_signature_only_change(original, synthetic_macho(b'SYNTHETIC SMALL', linkedit_vm_size=32768))


@pytest.mark.parametrize('allocation', [129, 136, 159])
def test_signed_allocation_must_match_modern_16_byte_contract(allocation):
    with pytest.raises(ValueError, match='macho_delta_signature_allocation_alignment'):
        delta.verify_signature_only_change(synthetic_macho(),
            synthetic_macho(b'SYNTHETIC NEW', signature_size=allocation))


def synthetic_thread_zerofill():
    # Reclassify __DATA's synthetic section as an observed mapped thread_bss.
    raw = synthetic_macho()
    pos = command_offset(raw, delta.LC_SEGMENT_64, occurrence=1) + 72
    raw = change(raw, pos + 64, 0x12)
    raw = raw[:DATA_OFFSET] + b'\0' * 32 + raw[DATA_OFFSET + 32:]
    return raw


def test_nonzero_thread_zerofill_offset_is_vm_metadata_and_file_padding_remains_immutable():
    original = synthetic_thread_zerofill()
    assert delta.inspect_signing_input(original)['evidence'] == 'ORIGINAL_LAYOUT_ONLY'
    signed = original[:SIGNATURE_OFFSET] + synthetic_signature(b'SYNTHETIC NEW')
    delta.verify_signature_only_change(original, signed)
    pos = command_offset(original, delta.LC_SEGMENT_64, occurrence=1) + 72
    for damaged in (change(signed, pos + 48, DATA_OFFSET + 16),
                    change(signed, DATA_OFFSET, 1), change(signed, pos + 64, 1),
                    change(signed, pos + 32, 0x100001010, '<Q')):
        with pytest.raises(ValueError, match='macho_delta_'):
            delta.verify_signature_only_change(original, damaged)


def test_addition_compares_every_preserved_original_byte():
    original = synthetic_unsigned_macho(trim=7)
    signed = synthetic_added_signature(original)
    evidence = delta.verify_signature_only_change(original, signed)
    exempt = {i for field in evidence['mutable_load_command_fields']
              for i in range(field['offset'], field['offset'] + field['size'])}
    for offset in range(len(original)):
        if offset not in exempt:
            damaged = bytearray(signed)
            damaged[offset] ^= 1
            with pytest.raises(ValueError, match='macho_delta_'):
                delta.verify_signature_only_change(original, bytes(damaged))
