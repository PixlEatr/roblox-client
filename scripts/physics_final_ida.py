"""Targeted read-only IDA export for the physics call-chain wave.

Run on the disposable physics-wave.i64. Writes only outputs/physics-final/ida-*.
Requests can be appended to TARGETS after peer analysts identify concrete
addresses. This intentionally avoids modifying the binary or original IDB.
"""
import json
import hashlib
from pathlib import Path
import traceback

import ida_auto, ida_bytes, ida_funcs, ida_hexrays, ida_idaapi, ida_lines, ida_loader, ida_nalt, ida_pro, ida_ua
import idautils, idc

ROOT = Path(idc.ARGV[1]).resolve()
OUT = ROOT / ("outputs/physics-final/ida-close-266" if "--close-266" in idc.ARGV
              else "outputs/physics-final/ida-callback-parser" if "--callback-parser" in idc.ARGV
              else "outputs/physics-final/ida-taskqueue-deeper" if "--taskqueue-deeper" in idc.ARGV
              else "outputs/physics-final/ida-replicator-audit-fresh" if "--replicator-audit-fresh" in idc.ARGV
              else "outputs/physics-final/ida-replicator-audit" if "--replicator-audit" in idc.ARGV
              else "outputs/physics-final/ida-rna-layout" if "--rna-layout" in idc.ARGV
              else "outputs/physics-final/ida-mega-source" if "--mega-source" in idc.ARGV
              else "outputs/physics-final/ida-rna-transport" if "--rna-transport" in idc.ARGV
              else "outputs/physics-final/ida-record-producer" if "--record-producer" in idc.ARGV
              else "outputs/physics-final/ida-source-producer" if "--source-producer" in idc.ARGV
              else "outputs/physics-final/ida-layout-audit" if "--layout-audit" in idc.ARGV
              else "outputs/physics-final/ida-clock-a1" if "--clock-a1" in idc.ARGV
              else "outputs/physics-final")
TARGETS = {
    0x104CD1DD0: "object_reference_writer",
    0x104CD1E48: "physics_reference_writer",
    0x104F23D80: "physics_reference_type_tag_writer",
    0x1015F9230: "raw_fixed_width_writer",
    0x1015F943C: "null_reference_writer",
    0x1015F9530: "varuint_writer",
    0x104D4F89C: "optional_prefix_a",
    0x104D4FA50: "optional_prefix_b",
    # Existing top-level send/record helpers, exported again into this wave's
    # isolated output namespace to keep evidence and xrefs together.
    0x104D4C650: "packet_flush_dispatch",
    0x104D4C9C8: "physics_header_writer",
    0x104DBE5F4: "physics_header_order_selector",
    0x104DC0EFC: "counted_physics_byte_writer",
    0x104DC0F00: "counted_physics_byte_writer_impl",
    0x102C0C59C: "ts_scheduler_step_callback_candidate",
    0x102C0C64C: "ts_scheduler_cycle_helper_candidate",
    0x102C0C710: "ts_scheduler_registration_helper_candidate",
    0x102C0C914: "ts_scheduler_step_callback_body",
    0x102C0C7F0: "ts_scheduler_copy_or_update_candidate",
    0x104C9D9CC: "replicator_ctor_reference_candidate",
    0x104C9E150: "client_replicator_ctor_true_start_candidate",
    0x104C92A80: "transport_context_ea0_consumer_candidate",
    0x104C94940: "transport_context_lifecycle_candidate",
    0x104C8B21C: "transport_context_ea0_producer_candidate",
    0x104C98F30: "transport_shared_capture_candidate",
    0x104C8B8DC: "transport_context_ea0_producer_exact",
    0x104C9E348: "transport_shared_capture_dispatch_target",
    0x1015F9230: "raw_fixed_width_writer",
    0x104CC0320: "peer_queue_or_dispatch",
    0x104CC06BC: "peer_enqueue_record",
    0x104CC2734: "peer_deferred_dispatch",
    0x104CC31B8: "raknet_peer_slot56_target",
    0x104CC2FCC: "raknet_peer_method_adjacent_a",
    0x104CC317C: "raknet_peer_method_adjacent_b",
    0x104CC4C44: "raknet_peer_method_adjacent_c",
    0x104CC2F4C: "raknet_connection_peer_store",
    0x104CC2EE8: "raknet_connection_base_ctor",
    0x104CC38C4: "raknet_connection_ctor_chain",
    0x1044C58D0: "raknet_connection_base_init",
    0x1015EECC4: "network_transport_mode_selector",
    0x10004F1C0: "peer_slot192_candidate",
    0x102AAE57C: "raknet_peer_slot192_candidate",
    0x102114A60: "quic_mux_slot56_target",
    0x102114EA4: "quic_mux_slot192_target",
    0x102156AB8: "webtransport_mux_slot56_target",
    0x102157418: "webtransport_mux_slot192_target",
    0x102117350: "rtc_app_slot56_target",
    0x102117D88: "rtc_app_slot192_target",
    0x1044C70B8: "raknet_connection_slot192_target",
    0x1044C70C4: "raknet_connection_slot192_impl",
    0x1044C5FFC: "rbx_transport_connection_slot56_target",
    0x102114D40: "quic_mux_slot128_target",
    0x102156EDC: "webtransport_mux_slot128_target",
    0x102117E58: "rtc_app_slot128_target",
    0x102114B48: "quic_channel_slot80_target",
    0x102114E8C: "quic_channel_slot168_target",
    0x1021D6054: "quic_channel_slot80_impl",
    0x102156CD0: "webtransport_channel_slot80_target",
    0x1021572FC: "webtransport_channel_slot168_target",
    0x1021746EC: "webtransport_channel_slot80_impl",
    0x102139588: "rbx_transport_protocol_channel_slot80_target",
    0x102139ECC: "rbx_transport_protocol_channel_slot168_target",
    0x102139ED8: "rbx_transport_protocol_channel_slot176_target",
    0x1021D5F60: "quic_mux_slot56_impl",
    0x1021D6458: "quic_mux_slot192_impl",
    0x10222BAA0: "rtc_app_slot56_impl",
    0x10212681C: "rtc_app_slot192_impl",
    0x102E86744: "quaternion_extract_helper",
    0x1048E1A74: "orientation_expander_caller_1048e1a74",
    0x1048EDE18: "generic_coordinateframe_translation_reader",
    0x1048EE24C: "generic_coordinateframe_orientation_id_reader",
    0x1048EE1B4: "orientation_expander_caller_1048ee1b4",
    0x104D48680: "orientation_expander_caller_104d48680",
    0x104F211D8: "position_codec_caller_104f211d8",
    0x104F213A4: "orientation_expander_caller_104f213a4",
    0x104F21500: "position_codec_caller_104f21500",
    0x104F216CC: "position_codec_caller_104f216cc",
    0x104F2189C: "orientation_expander_caller_104f2189c",
    0x104F219F8: "orientation_expander_caller_104f219f8",
    0x104F23698: "orientation_expander_caller_104f23698",
    0x104F23880: "orientation_expander_caller_104f23880",
    0x104F233B0: "packed_transform_reader_candidate_104f233b0",
    0x104F2350C: "packed_transform_reader_candidate_104f2350c",
    0x104DA616C: "property_codec_dispatch_callsite_104da616c",
    0x104DAA598: "property_codec_dispatch_callsite_104daa598",
    0x1015FA0D8: "compact_orientation_selector_reader",
    0x104F21168: "selector31_transform_reader",
    0x1044C5768: "rbx_transport_connection_ctor_candidate",
    0x1044C67F0: "rbx_transport_connection_factory_callsite_a",
    0x1044C6ACC: "rbx_transport_connection_factory_callsite_b",
    0x104C8B8DC: "rbx_transport_registration_candidate",
    0x1021168A4: "rtc_app_connection_ctor_candidate_a",
    0x102116D3C: "rtc_app_connection_ctor_candidate_b",
    0x10211706C: "rtc_app_connection_ctor_candidate_c",
    0x104DB2FEC: "network_replicator_constructor_candidate",
    0x104C9E1C0: "replicator_ctor_callsite_a",
    0x104E247D0: "replicator_ctor_callsite_b",
    0x104DD6080: "process_deserialized_join_data_item_handler",
    0x104D17A78: "new_instance_handler_helper",
    0x104DD5CF4: "join_item_post_dispatch",
    0x104DB28B8: "join_item_class_or_time_lookup",
    0x104D8DFEC: "queue_unresolved_join_item",
    0x104D8B064: "waiting_item_vector_grow",
    0x104D8B1AC: "waiting_item_vector_append",
    0x104DFE02C: "get_waiting_children_bucket",
    0x104D22EC4: "scoped_instance_lookup",
    0x104DB2C60: "physics_clock_adjustment_lookup",
    0x104DB2C40: "physics_clock_adjustment_convert",
    0x102C0A078: "physics_clock_monotonic_candidate",
    0x1011FFFD8: "workspace_time_candidate",
    0x1017341D0: "workspace_time_scale_candidate",
    0x100F15D38: "workspace_time_integer_candidate",
    0x104D4BF90: "physics_record_candidate_builder",
    0x102C0C0F0: "physics_clock_object_init_candidate",
    0x102C92AE8: "mach_absolute_time_conversion_candidate",
    0x102C9271C: "mach_absolute_time_fallback_candidate",
    0x102C923EC: "native_clock_gettime_implementation",
    0x102137EF8: "invalid_reason_class_string_mapper",
    0x102160DA8: "h3_connection_close_receiver",
    0x1021C71D4: "reason_class_code_string_mapper",
    0x1022285E4: "transport_disconnect_reason_formatter",
    0x102229B80: "transport_disconnect_reason_formatter_b",
    0x104C93720: "replicator_disconnect_reason_mapping",
    0x104C8E3E8: "network_client_disconnect_reason_setter",
    0x102ABB48C: "raknet_disconnect_reason_receiver",
    0x104EEE9E4: "dummy_client_close_reason_handler",
    0x1015FC948: "serialized_reason_enum_string",
    0x1015FCAA0: "serialized_reason_text",
    0x1015EF460: "player_disconnect_reason_enum_string",
    0x103AEA748: "disconnect_reason_class_value_mapper_a",
    0x103EB93E4: "disconnect_reason_class_value_mapper_b",
    0x104DBA8CC: "client_disconnect_reason_message_switch",
    0x104D4B8C0: "physics_send_worker_caller_a",
    0x104D4C034: "physics_send_worker_caller_b",
    0x104D4C0A0: "physics_send_worker_caller_c",
    0x102AAE734: "raknet_peer_send_serializer",
    0x102AADF14: "raknet_peer_send_ready_filter",
    0x102AA9928: "raknet_peer_vtable_store_candidate_a",
    0x102AAA354: "raknet_peer_vtable_store_candidate_b",
    0x104D4D76C: "root_record_writer",
    0x104D4DBB0: "root_history_wrapper",
    0x104D4DCEC: "child_traversal",
    0x104D4E704: "primary_body_writer",
    0x104D4DE2C: "children_vector_writer",
    0x104D4F5A8: "child_transform_writer",
    0x102E8350C: "child_scalar_pack_helper",
    0x104CBEF68: "child_scalar_writer",
    0x102E86744: "quaternion_extract_helper",
    0x104D4DEB0: "history_body_writer",
    0x104D4EF84: "root_transform_writer",
    0x102E8F40C: "packed_orientation_expander",
    0x104CBE7B4: "position_decoder_or_encoder",
    0x104D4AEF8: "scheduled_sender_worker",
    0x104D4CDD0: "record_body_populator",
    0x104D4CDD0: "record_body_populator",
    0x104D4AD24: "sender_state_ctor_caller",
    0x104D4A53C: "sender_registration_candidate_a",
    0x104D4A5D0: "sender_registration_candidate_b",
    0x104D4AAF8: "sender_state_ctor_candidate_a",
    0x104D4AB60: "sender_state_ctor_candidate_b",
    0x104D4BCDC: "sender_clock_init_candidate",
    0x104D4BDC0: "sender_config_candidate_a",
    0x104D4BF34: "sender_config_candidate_b",
    0x104D4C0D0: "sender_config_candidate_c",
    0x104D4C32C: "sender_config_candidate_d",
    0x104D4FA50: "cached_record_callback",
    0x104D51F78: "history_append_callback_a",
    0x104D52160: "history_append_callback_b",
    0x10211BA6C: "open_unreliable_channel_receive_candidate",
    0x102117F88: "open_unreliable_channel_send_candidate",
    0x10211BB50: "open_unreliable_channel_receive_candidate_b",
    0x1044C6448: "transport_open_channels_candidate",
    0x1044C5FD0: "transport_open_channel_parameters",
    0x10212DE10: "locally_opened_stream_ack_candidate",
}

KEYWORDS = ("NetworkOwnerV3", "NetworkOwnershipRule", "GetNetworkOwner",
            "SetNetworkOwner", "GetNetworkOwnershipAuto", "PhysicsRepRootPart",
            "PhysicsRepRootRef", "AuthorityMode", "AutoSimulate", "LocalClient")
# Fixed addresses from the already-saved strings index. Avoid rebuilding the
# entire IDA string list in a batch process; that scan dominates startup on
# this 500+ MB database and is not needed for the explicit call-chain exports.
STRING_ADDRESSES = {
    0x105F3234E: "NetworkOwnerV3",
    0x105F32087: "GetNetworkOwner",
    0x105F32AEA: "NetworkOwnershipRule",
    0x105EA9ED7: "NetworkOwnerJob",
    0x10616E0F5: "ServerAuthorityPredictionStatusUseComputedNetworkOwner",
    0x10621402F: "OptimizeManualNetworkOwnerCheck",
    0x105EA8130: "transport_send_tuple_a",
    0x105EA8168: "transport_send_tuple_b",
    0x105EA81A0: "transport_send_tuple_c",
}
CLASS_DEFAULT_STRING_ADDRESSES = {
    0x1059AE141: "Part_PartType_enum_property_descriptor",
    0x1059AE19E: "Part_PartType_property_descriptor_impl",
    0x1059B08BC: "Part_creatable_descriptor",
    0x1059A8E19: "FormFactorPart_class_descriptor",
    0x1059A8EC1: "FormFactorPart_FormFactor_property_descriptor",
    0x105951417: "PartType_enum_descriptor",
    0x105EE6B6C: "Shape_property_name",
    0x105F22990: "Block_enum_name",
    0x105F235B6: "PartType_enum_name",
    0x105F32266: "CanCollide_property_name",
    0x1059EA865: "Reflection_ClassDescriptor_RTTI_name",
    0x10617E13A: "newInstanceId_property_or_field_name",
    0x1061CD766: "NewInstance_name",
    0x1061CD772: "NewInstanceGuid_name",
    0x106206E9C: "newInstance_log_format",
    0x106232592: "dummy_client_open_time_sync_channel_log",
    0x106232E7F: "dummy_client_time_sync_send_error_log",
    0x106232EC1: "dummy_client_time_sync_invalid_message_log",
    0x106232F19: "dummy_client_time_sync_non_sync_message_log",
    0x106232F79: "dummy_client_time_sync_non_monotonic_log",
    0x106233E5B: "dummy_server_time_sync_invalid_message_log",
    0x106233EB3: "dummy_server_time_sync_non_sync_message_log",
    0x106233F13: "dummy_server_time_sync_send_error_log",
    0x106234B5A: "ClassDescriptor_name",
    0x106234844: "NetworkFilterCloudEditNewInstanceItemProperties_flag",
}


def get_func(ea):
    f = ida_funcs.get_func(ea)
    if f is not None and f.start_ea == ea:
        return f
    # Do not read cached JSON from outputs/ida here. Concurrent analysts may
    # be writing that large shared tree; infer a bounded function directly
    # in this disposable database instead.
    ida_ua.create_insn(ea)
    ida_funcs.add_func(ea, ida_idaapi.BADADDR)
    ida_auto.plan_and_wait(ea, ea + 0x500)
    f = ida_funcs.get_func(ea)
    return f if f is not None and f.start_ea == ea else None


def export(ea, label, source):
    item = {"address": hex(ea), "label": label, "source": source}
    f = get_func(ea)
    if f is None:
        item["error"] = "no function at address"
        return item
    item["end"] = hex(f.end_ea)
    item["name"] = idc.get_name(ea)
    item["callers"] = [hex(x) for x in idautils.CodeRefsTo(ea, False)]
    item["data_xrefs_to"] = [hex(x) for x in idautils.DataRefsTo(ea)]
    asm = []
    for insn in idautils.FuncItems(ea):
        line = ida_lines.tag_remove(idc.generate_disasm_line(insn, 0) or "")
        asm.append("%016x  %s" % (insn, line))
    stem = "ida-%s-%x" % (label, ea)
    (OUT / (stem + ".asm")).write_text("\n".join(asm) + "\n")
    if ida_hexrays.init_hexrays_plugin():
        try:
            cf = ida_hexrays.decompile(ea)
            if cf is not None:
                (OUT / (stem + ".c")).write_text(str(cf) + "\n")
                item["decompiled"] = True
            else:
                item["decompiled"] = False
        except Exception as exc:
            item["decompiled"] = False
            item["decompiler_error"] = str(exc)
    return item


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    progress_path = OUT / "ida-progress.log"
    progress_path.write_text("script entered\n")
    if "--rna-transport" in idc.ARGV:
        # Bounded, read-only class path query for the RNA experimental transport.
        # The strings are addresses from the pinned binary's extracted string index.
        getter = 0x1021265E4
        class_string = 0x105FFB5BD
        getter_log = 0x105FFB950
        targets = {"getter": getter, "class_name_string": class_string,
                   "get_capabilities_log": getter_log,
                   "record_state_setter": 0x104D46074,
                   "record_state_owner_accessor": 0x1011AF9F4,
                   "humanoid_network_state_string": 0x105EEA190}
        try:
            input_path = ida_nalt.get_input_file_path()
        except Exception:
            input_path = None
        result = {"static_only": True, "database_saved": False,
                  "input_path": input_path,
                  "targets": {}, "candidate_vtable_refs": []}
        # Recreate only these three LC_FUNCTION_STARTS ranges in the disposable
        # database, avoiding stale auto-analysis boundaries.
        exact_ranges = ((0x1021265E4, 0x10212674C, "rna_get_capabilities_method"),
                        (0x104D46074, 0x104D46548, "record_state_setter"),
                        (0x1011AF9F4, 0x1011AF9F8, "record_state_owner_accessor"))
        for start, end, label in exact_ranges:
            for old in list(idautils.Functions(start, end)):
                ida_funcs.del_func(old)
            ida_bytes.del_items(start, ida_bytes.DELIT_SIMPLE, end - start)
            ea = start
            while ea < end:
                size = ida_ua.create_insn(ea)
                if size <= 0 or ea + size > end:
                    break
                ea += size
            if ea == end and ida_funcs.add_func(start, end):
                ida_auto.plan_and_wait(start, end)
        for label, ea in targets.items():
            f = ida_funcs.get_func(ea)
            refs = list(idautils.XrefsTo(ea, 0))
            result["targets"][label] = {
                "address": hex(ea), "name": idc.get_name(ea),
                "containing_function": hex(f.start_ea) if f else None,
                "function_end": hex(f.end_ea) if f else None,
                "code_refs_to": [hex(x) for x in idautils.CodeRefsTo(ea, False)],
                "data_refs_to": [hex(x) for x in idautils.DataRefsTo(ea)],
                "xrefs_to": [{"from": hex(x.frm), "type": x.type,
                               "asm": ida_lines.tag_remove(idc.generate_disasm_line(x.frm, 0) or ""),
                               "containing_function": (hex(ida_funcs.get_func(x.frm).start_ea)
                                   if ida_funcs.get_func(x.frm) else None)} for x in refs]}
            if label in ("getter", "record_state_setter", "record_state_owner_accessor") and f and f.start_ea == ea:
                result.setdefault("function_exports", []).append(
                    export(ea, label, "exact_lc_function_start"))
        # Surface only vtable/data references IDA already knows about; no image-wide scan.
        for ea in idautils.DataRefsTo(getter):
            result["candidate_vtable_refs"].append({
                "reference": hex(ea),
                "containing_function": (hex(ida_funcs.get_func(ea).start_ea)
                                         if ida_funcs.get_func(ea) else None),
                "asm": ida_lines.tag_remove(idc.generate_disasm_line(ea, 0) or ""),
                "qword_at_reference": hex(ida_bytes.get_qword(ea & ~7))})
        state_ref_functions = {}
        for ref in idautils.DataRefsTo(targets["humanoid_network_state_string"]):
            f = ida_funcs.get_func(ref)
            if f:
                state_ref_functions[f.start_ea] = f.end_ea
        result["humanoid_state_registration_functions"] = []
        for start, end in sorted(state_ref_functions.items()):
            result["humanoid_state_registration_functions"].append({
                "start": hex(start), "end": hex(end),
                "data_refs_to_string": [hex(ref) for ref in idautils.DataRefsTo(targets["humanoid_network_state_string"])
                                        if (ida_funcs.get_func(ref) and ida_funcs.get_func(ref).start_ea == start)]})
            result.setdefault("function_exports", []).append(
                export(start, "network_humanoid_state_string_ref", "string_reference_function"))
        (OUT / "ida-rna-transport-manifest.json").write_text(json.dumps(result, indent=2) + "\n")
        progress_path.write_text("bounded RNA transport refs complete\n")
        print("bounded RNA transport refs complete", flush=True)
        return
    if "--mega-source" in idc.ARGV:
        # Bound analysis to the confirmed MegaReplicator vtable and constructor.
        ap, ctor, producer, leaf = 0x1069C69F8, 0x104D11BC4, 0x104D1837C, 0x104D17CAC
        result = {"static_only": True, "database_saved": False,
                  "vtable_address_point": hex(ap), "constructor": hex(ctor),
                  "source_vector_producer": hex(producer), "record_leaf_writer": hex(leaf),
                  "vtable_slots": [], "field_3f8_users": []}
        result["xref_queries"] = {}
        for label, target in (("vtable_address_point", ap), ("constructor", ctor),
                              ("source_vector_producer", producer), ("record_leaf_writer", leaf)):
            result["xref_queries"][label] = {
                "target": hex(target),
                "code_refs_to": [hex(x) for x in idautils.CodeRefsTo(target, False)],
                "data_refs_to": [hex(x) for x in idautils.DataRefsTo(target)],
                "all_xrefs_to": [{"from": hex(x.frm), "type": x.type,
                                  "asm": ida_lines.tag_remove(idc.generate_disasm_line(x.frm, 0) or ""),
                                  "function": (hex(ida_funcs.get_func(x.frm).start_ea)
                                               if ida_funcs.get_func(x.frm) else None)}
                                 for x in idautils.XrefsTo(target, 0)]}
        methods = set()
        for slot in range(48):
            value = ida_bytes.get_qword(ap + slot * 8)
            f = ida_funcs.get_func(value) if value else None
            row = {"slot": slot, "entry": hex(ap + slot * 8),
                   "value": hex(value) if value else None,
                   "name": idc.get_name(value) if value else None,
                   "function_start": hex(f.start_ea) if f else None,
                   "function_end": hex(f.end_ea) if f else None}
            result["vtable_slots"].append(row)
            if f and f.start_ea == value:
                methods.add(value)
        for ea in sorted(methods | {ctor, producer, leaf}):
            f = ida_funcs.get_func(ea)
            if not f:
                continue
            asm = []
            for insn_ea in idautils.FuncItems(ea):
                line = ida_lines.tag_remove(idc.generate_disasm_line(insn_ea, 0) or "")
                asm.append(line)
                if "#0x3F8" in line or "#1016" in line:
                    result["field_3f8_users"].append({"function": hex(ea), "instruction": hex(insn_ea), "asm": line})
            if any(hex(ea) == x["function"] for x in result["field_3f8_users"]):
                result.setdefault("field_3f8_function_exports", []).append(export(ea, "mega_class_3f8_accessor", "vtable_or_ctor"))
        (OUT / "ida-mega-source-manifest.json").write_text(json.dumps(result, indent=2) + "\n")
        progress_path.write_text("bounded MegaReplicator vtable scan complete\n")
        print("bounded MegaReplicator vtable scan complete", flush=True)
        return
    if "--rna-layout" in idc.ARGV:
        # Exact-offset scan, bounded to the transport code area containing the
        # confirmed RNA logger/getter references. No global function scan.
        lo, hi = 0x102110000, 0x10213A000
        interested = (0xCF38, 0xCE70, 0xCF40, 0xCF30)
        hits = []
        funcs = []
        for fstart in idautils.Functions(lo, hi):
            f = ida_funcs.get_func(fstart)
            if not f or f.start_ea < lo or f.start_ea >= hi:
                continue
            has_interesting = False
            for ea in idautils.FuncItems(f.start_ea):
                insn = ida_ua.insn_t()
                if ida_ua.decode_insn(insn, ea) <= 0:
                    continue
                operands = [op.addr for op in insn.ops if op.type == ida_ua.o_displ]
                immediates = [op.value for op in insn.ops if op.type == ida_ua.o_imm]
                mnem = (idc.print_insn_mnem(ea) or "").upper()
                if any(x in interested for x in operands + immediates):
                    has_interesting = True
                    hits.append({"function": hex(f.start_ea), "ea": hex(ea),
                                 "mnem": mnem,
                                 "operands": [hex(x) for x in operands],
                                 "immediates": [hex(x) for x in immediates],
                                 "asm": ida_lines.tag_remove(idc.generate_disasm_line(ea, 0) or "")})
            if has_interesting:
                funcs.append(f.start_ea)
        string_refs = [0x102117648, 0x102117F88, 0x10211BA6C,
                       0x1021265E4, 0x102126A0C, 0x10212DE10]
        result = {"static_only": True, "database_saved": False,
                  "scanned_function_address_range": [hex(lo), hex(hi)],
                  "offsets_checked": [hex(x) for x in interested],
                  "exact_offset_hits": hits,
                  "functions_with_hits": [hex(x) for x in sorted(set(funcs))],
                  "known_logger_reference_functions": []}
        for ea in string_refs:
            f = ida_funcs.get_func(ea)
            result["known_logger_reference_functions"].append({
                "address": hex(ea), "function": hex(f.start_ea) if f else None,
                "end": hex(f.end_ea) if f else None,
                "name": idc.get_name(f.start_ea) if f else None})
            if f and f.start_ea == ea:
                result.setdefault("logger_reference_exports", []).append(
                    export(ea, "rna_logger_ref_context", "bounded_string_reference"))
        (OUT / "ida-rna-layout-manifest.json").write_text(json.dumps(result, indent=2) + "\n")
        progress_path.write_text("bounded RNA exact-offset scan complete\n")
        print("bounded RNA exact-offset scan complete", flush=True)
        return
    if "--replicator-audit" in idc.ARGV:
        ranges = ((0x104D19610, 0x104D19860, "client_direct_current_parser"),
                  (0x104D4D76C, 0x104D4DBB0, "root_record_writer_comparison"),
                  (0x104D4DBB0, 0x104D4DCEC, "history_suffix_writer_comparison"))
        exact = []
        for start, end, label in ranges:
            removed = [f for f in idautils.Functions(start, end)]
            for old in removed:
                ida_funcs.del_func(old)
            ida_bytes.del_items(start, ida_bytes.DELIT_SIMPLE, end - start)
            ea = start
            while ea < end:
                size = ida_ua.create_insn(ea)
                if size <= 0 or ea + size > end:
                    break
                ea += size
            ok = ea == end and ida_funcs.add_func(start, end)
            if ok:
                ida_auto.plan_and_wait(start, end)
            f = ida_funcs.get_func(start)
            exact.append({"start": hex(start), "end_exclusive": hex(end),
                          "decoded_end": hex(ea), "add_succeeded": bool(ok),
                          "actual_start": hex(f.start_ea) if f else None,
                          "actual_end": hex(f.end_ea) if f else None,
                          "label": label, "removed_starts": [hex(x) for x in removed]})
        result = {"static_only": True, "database_saved": False,
                  "exact_lc_ranges": exact, "functions": [], "parser_callees": [],
                  "callers": [], "queue_consumer_vtable_scan": []}
        target = 0x104D19610
        tf = ida_funcs.get_func(target)
        bl_targets = []
        if tf and tf.start_ea == target:
            for ea in idautils.FuncItems(target):
                if idc.print_insn_mnem(ea).upper() == "BL":
                    dest = idc.get_operand_value(ea, 0)
                    f = ida_funcs.get_func(dest)
                    bl_targets.append({"site": hex(ea), "target": hex(dest),
                                       "name": idc.get_name(dest),
                                       "function_start": hex(f.start_ea) if f else None,
                                       "function_end": hex(f.end_ea) if f else None})
        for ea, label in ((0x104D19610, "client_direct_current_parser"),
                          (0x104D4D76C, "root_record_writer_comparison"),
                          (0x104D4DBB0, "history_suffix_writer_comparison")):
            result["functions"].append(export(ea, label, "exact_lc_function_start"))
        result["direct_bl_targets"] = bl_targets
        seen = set()
        for item in bl_targets:
            dest = int(item["target"], 16)
            f = ida_funcs.get_func(dest)
            if f and f.start_ea == dest and dest not in seen and len(seen) < 16:
                seen.add(dest)
                result["parser_callees"].append(export(dest, "client_parser_direct_callee", "bounded_direct_bl"))
        for ref in idautils.CodeRefsTo(target, False):
            f = ida_funcs.get_func(ref)
            result["callers"].append({"site": hex(ref), "function": hex(f.start_ea) if f else None,
                                      "function_end": hex(f.end_ea) if f else None,
                                      "asm": ida_lines.tag_remove(idc.generate_disasm_line(ref, 0) or "")})
            if f and f.start_ea != target:
                result["functions"].append(export(f.start_ea, "client_parser_caller", "direct_code_ref"))
        result["target_data_refs_to"] = [hex(x) for x in idautils.DataRefsTo(target)]
        # Scan only the confirmed ServerReplicator and MegaReplicator vtable
        # entries for reads/writes of the three pending-vector fields written
        # by D19610 (this + 0xAA8/+0xAB0/+0xAB8). This is not a global scan.
        for ap, class_label in ((0x1069CB2B0, "ServerReplicator"),
                                (0x1069C69F8, "MegaReplicator")):
            for slot in range(64):
                entry = ap + slot * 8
                dest = ida_bytes.get_qword(entry)
                f = ida_funcs.get_func(dest) if dest else None
                slot_row = {"class_candidate": class_label, "address_point": hex(ap),
                            "slot": slot, "target": hex(dest) if dest else None,
                            "function_start": hex(f.start_ea) if f else None,
                            "function_end": hex(f.end_ea) if f else None,
                            "matching_instructions": []}
                if f and f.start_ea == dest:
                    for ea in idautils.FuncItems(dest):
                        insn = ida_ua.insn_t()
                        if ida_ua.decode_insn(insn, ea) <= 0:
                            continue
                        values = [op.addr for op in insn.ops if op.type == ida_ua.o_displ]
                        values += [op.value for op in insn.ops if op.type == ida_ua.o_imm]
                        if any(v in (0xAA8, 0xAB0, 0xAB8, 0x2C88, 0x2C90, 0x2C98) for v in values):
                            slot_row["matching_instructions"].append({
                                "ea": hex(ea), "asm": ida_lines.tag_remove(idc.generate_disasm_line(ea, 0) or "")})
                    if slot_row["matching_instructions"]:
                        result["queue_consumer_vtable_scan"].append(slot_row)
        (OUT / "ida-replicator-audit-manifest.json").write_text(json.dumps(result, indent=2) + "\n")
        progress_path.write_text("bounded client direct-current parser audit complete\n")
        print("bounded client direct-current parser audit complete", flush=True)
        return
    if "--replicator-audit-fresh" in idc.ARGV:
        # A new IDB is created from the pinned binary with IDA autoanalysis
        # disabled. Parse only LC_FUNCTION_STARTS and the two known vtables.
        import bisect, sys
        sys.path.insert(0, str(ROOT / "scripts"))
        from macho_scan import parse_macho
        image = Path("/tmp/roblox-physics-analysis-arm64").read_bytes()
        layout = parse_macho(image)
        starts = layout["functions"]
        md = {"static_only": True, "database_saved": False,
              "input_path": ida_nalt.get_input_file_path(),
              "known_db_error": "fresh isolated DB; autoanalysis disabled",
              "vectors": [], "matching_methods": []}
        watched = {0xAA8, 0xAB0, 0xAB8, 0x2C88, 0x2C90, 0x2C98}
        for ap, cls in ((0x1069CB2B0, "ServerReplicator"),
                        (0x1069C69F8, "MegaReplicator")):
            for slot in range(64):
                ea = ap + slot * 8
                value = ida_bytes.get_qword(ea)
                row = {"class_candidate": cls, "address_point": hex(ap),
                       "slot": slot, "entry": hex(ea),
                       "target": hex(value) if value else None}
                if value and value in starts:
                    idx = bisect.bisect_left(starts, value)
                    end = starts[idx + 1] if idx + 1 < len(starts) else value
                    row["function_end"] = hex(end)
                    hits = []
                    decoded = []
                    pos = value
                    while pos < end:
                        insn = ida_ua.insn_t()
                        size = ida_ua.decode_insn(insn, pos)
                        if size <= 0 or pos + size > end:
                            break
                        line = ida_lines.tag_remove(idc.generate_disasm_line(pos, 0) or "")
                        decoded.append("%016x  %s" % (pos, line))
                        values = [op.addr for op in insn.ops if op.type == ida_ua.o_displ]
                        values += [op.value for op in insn.ops if op.type == ida_ua.o_imm]
                        if any(v in watched for v in values):
                            hits.append({"ea": hex(pos), "asm": line})
                        pos += size
                    row["decoded_end"] = hex(pos)
                    row["matching_instructions"] = hits
                    if hits:
                        md["matching_methods"].append(row)
                        stem = "method-%s-slot-%02d-%x" % (cls.lower(), slot, value)
                        (OUT / (stem + ".asm")).write_text("\n".join(decoded) + "\n")
                md["vectors"].append(row)
        (OUT / "ida-replicator-audit-fresh-manifest.json").write_text(json.dumps(md, indent=2) + "\n")
        progress_path.write_text("fresh isolated vtable method scan complete\n")
        print("fresh isolated vtable method scan complete", flush=True)
        return
    if "--taskqueue-deeper" in idc.ARGV:
        ranges = ((0x102C054E8, 0x102C05984, "ppr_task_queue_process_wrapper"),
                  (0x102C061C0, 0x102C0658C, "ppr_task_wait_callback_dispatch"),
                  (0x102C0658C, 0x102C065F8, "ppr_task_wait_callback_helper"),
                  (0x102C09990, 0x102C09AB4, "task_queue_future_helper"),
                  (0x104D19B38, 0x104D1A124, "mega_replicator_queue_drain"),
                  (0x104D126C8, 0x104D128BC, "mega_replicator_queue_init"))
        rows = []
        for start, end, label in ranges:
            for old in list(idautils.Functions(start, end)):
                if old != start:
                    ida_funcs.del_func(old)
            ida_bytes.del_items(start, ida_bytes.DELIT_SIMPLE, end - start)
            pos = start
            while pos < end:
                size = ida_ua.create_insn(pos)
                if size <= 0 or pos + size > end:
                    break
                pos += size
            ok = pos == end and ida_funcs.add_func(start, end)
            f = ida_funcs.get_func(start)
            row = {"start": hex(start), "end_exclusive": hex(end), "decoded_end": hex(pos),
                   "add_succeeded": bool(ok), "actual_start": hex(f.start_ea) if f else None,
                   "actual_end": hex(f.end_ea) if f else None, "label": label}
            rows.append(row)
        results = []
        for start, end, label in ranges:
            results.append(export(start, label, "exact_lc_function_start"))
        result = {"static_only": True, "database_saved": False,
                  "database": ida_loader.get_path(ida_loader.PATH_TYPE_IDB),
                  "exact_lc_ranges": rows, "functions": results}
        (OUT / "ida-taskqueue-deeper-manifest.json").write_text(json.dumps(result, indent=2) + "\n")
        progress_path.write_text("bounded task queue dispatch exports complete\n")
        print("bounded task queue dispatch exports complete", flush=True)
        return
    if "--callback-parser" in idc.ARGV:
        ranges = ((0x104D297D8, 0x104D2983C, "ppr_callback_invoke"),
                  (0x104D2983C, 0x104D2984C, "ppr_callback_move"),
                  (0x104D2984C, 0x104D2985C, "ppr_callback_destroy"),
                  (0x104DD8160, 0x104DD8668, "client_direct_current_payload_parser"),
                  (0x1015F8FF8, 0x1015F90C8, "stream_varint_reader"),
                  (0x1015FA0D8, 0x1015FA0FC, "stream_small_value_reader"),
                  (0x104D47314, 0x104D47530, "replication_value_decode_helper"),
                  (0x1015F889C, 0x1015F88A0, "stream_reader_thunk"))
        rows = []
        for start, end, label in ranges:
            for old in list(idautils.Functions(start, end)):
                ida_funcs.del_func(old)
            ida_bytes.del_items(start, ida_bytes.DELIT_SIMPLE, end - start)
            pos = start
            while pos < end:
                size = ida_ua.create_insn(pos)
                if size <= 0 or pos + size > end:
                    break
                pos += size
            ok = pos == end and ida_funcs.add_func(start, end)
            f = ida_funcs.get_func(start)
            rows.append({"start": hex(start), "end_exclusive": hex(end), "decoded_end": hex(pos),
                         "add_succeeded": bool(ok), "actual_start": hex(f.start_ea) if f else None,
                         "actual_end": hex(f.end_ea) if f else None, "label": label})
        functions = [export(start, label, "exact_lc_function_start") for start, _, label in ranges]
        # Record direct call targets in the principal parser for the next
        # bounded follow-up without autoanalyzing the whole image.
        calls = []
        f = ida_funcs.get_func(0x104DD8160)
        if f and f.start_ea == 0x104DD8160:
            for ea in idautils.FuncItems(f.start_ea):
                if idc.print_insn_mnem(ea).upper() == "BL":
                    dest = idc.get_operand_value(ea, 0)
                    calls.append({"site": hex(ea), "target": hex(dest),
                                  "name": idc.get_name(dest)})
        result = {"static_only": True, "database_saved": False,
                  "database": ida_loader.get_path(ida_loader.PATH_TYPE_IDB),
                  "exact_lc_ranges": rows, "functions": functions,
                  "direct_bl_targets_from_parser": calls}
        (OUT / "ida-callback-parser-manifest.json").write_text(json.dumps(result, indent=2) + "\n")
        progress_path.write_text("bounded callback parser exports complete\n")
        print("bounded callback parser exports complete", flush=True)
        return
    if "--record-producer" in idc.ARGV:
        ranges = ((0x104D17CAC, 0x104D17DC8, "mega_record_leaf_writer"),
                  (0x104D1837C, 0x104D18AC8, "mega_record_vector_producer"),
                  (0x10107531C, 0x101075338, "array_item_to_instance_getter_10107531c"),
                  (0x100F819C0, 0x100F819F4, "record_property_getter_100f819c0"),
                  (0x10107B1DC, 0x10107B1F8, "record_instance_getter_10107b1dc"),
                  (0x101151E7C, 0x101151E90, "record_byte_getter_101151e7c"),
                  (0x100F83C58, 0x100F83C8C, "record_u32_getter_100f83c58"),
                  (0x1006798FC, 0x100679980, "record_nested_initializer_1006798fc"),
                  (0x104D2B800, 0x104D2B80C, "record_field_caller_104d2b800"),
                  (0x104D2B854, 0x104D2B860, "record_field_caller_104d2b854"))
        range_results = []
        for start, end, label in ranges:
            stale = list(idautils.Functions(start, end))
            for fstart in stale:
                ida_funcs.del_func(fstart)
            ida_bytes.del_items(start, ida_bytes.DELIT_SIMPLE, end - start)
            ea = start
            while ea < end:
                size = ida_ua.create_insn(ea)
                if size <= 0 or ea + size > end:
                    break
                ea += size
            ok = ida_funcs.add_func(start, end)
            if ok:
                ida_auto.plan_and_wait(start, end)
            f = ida_funcs.get_func(start)
            item = {"address": hex(start), "label": label,
                    "expected_end_exclusive": hex(end),
                    "add_func_succeeded": bool(ok), "decoded_end": hex(ea),
                    "function_start": hex(f.start_ea) if f else None,
                    "function_end": hex(f.end_ea) if f else None,
                    "callers": [hex(x) for x in idautils.CodeRefsTo(start, False)],
                    "data_xrefs_to": [hex(x) for x in idautils.DataRefsTo(start)],
                    "direct_bl_targets": []}
            if f and f.start_ea == start:
                calls = []
                asm = []
                for insn in idautils.FuncItems(start):
                    line = ida_lines.tag_remove(idc.generate_disasm_line(insn, 0) or "")
                    asm.append("%016x  %s" % (insn, line))
                    if idc.print_insn_mnem(insn).upper() == "BL":
                        tgt = idc.get_operand_value(insn, 0)
                        calls.append({"site": hex(insn), "target": hex(tgt),
                                      "name": idc.get_name(tgt),
                                      "function_start": (hex(ida_funcs.get_func(tgt).start_ea)
                                                         if ida_funcs.get_func(tgt) else None)})
                item["direct_bl_targets"] = calls
                stem = "ida-%s-%x" % (label, start)
                (OUT / (stem + ".asm")).write_text("\n".join(asm) + "\n")
                if ida_hexrays.init_hexrays_plugin():
                    try:
                        cf = ida_hexrays.decompile(start)
                        item["decompiled"] = cf is not None
                        if cf is not None:
                            (OUT / (stem + ".c")).write_text(str(cf) + "\n")
                    except Exception as exc:
                        item["decompiler_error"] = str(exc)
            range_results.append(item)
        result = {"database": ida_loader.get_path(ida_loader.PATH_TYPE_IDB),
                  "input_path": ida_nalt.get_input_file_path(),
                  "pinned_binary_sha256": hashlib.sha256(Path("/tmp/roblox-physics-analysis-arm64").read_bytes()).hexdigest(),
                  "static_only": True, "database_saved": False,
                  "exact_lc_function_ranges": range_results}
        (OUT / "ida-record-producer-manifest.json").write_text(json.dumps(result, indent=2) + "\n")
        progress_path.write_text("exact LC_FUNCTION_STARTS record producer exports complete\n")
        print("exact LC_FUNCTION_STARTS record producer exports complete", flush=True)
        return
    if "--source-producer" in idc.ARGV:
        # LC_FUNCTION_STARTS bounds verified from the pinned Mach-O:
        # 0x104C948F0 through (exclusive) 0x104C94ADC. The former IDB had an
        # erroneous mid-function boundary at 0x104C94940.
        start, end = 0x104C948F0, 0x104C94ADC
        existing_starts = [f for f in idautils.Functions(start, end)]
        for fstart in existing_starts:
            ida_funcs.del_func(fstart)
        ida_bytes.del_items(start, ida_bytes.DELIT_SIMPLE, end - start)
        ea = start
        while ea < end:
            size = ida_ua.create_insn(ea)
            if size <= 0 or ea + size > end:
                break
            ea += size
        added = ida_funcs.add_func(start, end)
        if added:
            ida_auto.plan_and_wait(start, end)
        bounded_ranges = ((0x104D121EC, 0x104D121F0, "source_parent_embedded_ctor_candidate_104d121ec"),
                          (0x104D11BC4, 0x104D121EC, "source_parent_embedded_ctor_impl_104d11bc4"),
                          (0x104D126C8, 0x104D128BC, "source_parent_embedded_init_candidate_104d126c8"),
                          (0x104C88820, 0x104C88D9C, "network_client_ctor_candidate_104c88820"))
        range_results = []
        for rstart, rend, _label in bounded_ranges:
            removed = [f for f in idautils.Functions(rstart, rend) if f != rstart]
            for fstart in removed:
                ida_funcs.del_func(fstart)
            ida_bytes.del_items(rstart, ida_bytes.DELIT_SIMPLE, rend - rstart)
            pos = rstart
            while pos < rend:
                size = ida_ua.create_insn(pos)
                if size <= 0 or pos + size > rend:
                    break
                pos += size
            ok = ida_funcs.add_func(rstart, rend)
            if ok and rend - rstart > 4:
                ida_auto.plan_and_wait(rstart, rend)
            range_results.append({"start": hex(rstart), "end_exclusive": hex(rend),
                                  "add_func_succeeded": bool(ok), "decoded_end": hex(pos),
                                  "removed_internal_starts": [hex(x) for x in removed]})
        targets = ((start, "source_producer_correct_lc_start_104c948f0"),
                   (0x104C93720, "source_producer_helper_104c93720"),
                   (0x104C92A80, "source_producer_consumer_104c92a80"),
                   (0x104D121EC, "source_parent_embedded_ctor_candidate_104d121ec"),
                   (0x104D11BC4, "source_parent_embedded_ctor_impl_104d11bc4"),
                   (0x104D126C8, "source_parent_embedded_init_candidate_104d126c8"),
                   (0x104C88820, "network_client_ctor_candidate_104c88820"))
        result = {"database": ida_loader.get_path(ida_loader.PATH_TYPE_IDB),
                  "input_path": ida_nalt.get_input_file_path(),
                  "pinned_binary_sha256": hashlib.sha256(Path("/tmp/roblox-physics-analysis-arm64").read_bytes()).hexdigest(),
                  "static_only": True, "database_saved": False,
                  "corrected_function_range": {"start": hex(start), "end_exclusive": hex(end),
                                                "old_midfunction_boundary_removed": existing_starts,
                                                "add_func_succeeded": bool(added), "decoded_end": hex(ea)},
                  "additional_lc_function_ranges": range_results,
                  "functions": []}
        for ea, label in targets:
            f = ida_funcs.get_func(ea)
            item = {"address": hex(ea), "label": label,
                    "exact_existing_start": bool(f and f.start_ea == ea),
                    "existing_function_start": hex(f.start_ea) if f else None,
                    "existing_function_end": hex(f.end_ea) if f else None,
                    "code_refs_to": [hex(x) for x in idautils.CodeRefsTo(ea, False)],
                    "data_refs_to": [hex(x) for x in idautils.DataRefsTo(ea)]}
            if f and f.start_ea == ea:
                asm = ["%016x  %s" % (insn, ida_lines.tag_remove(idc.generate_disasm_line(insn, 0) or ""))
                       for insn in idautils.FuncItems(ea) if insn < f.end_ea]
                stem = "ida-%s-%x" % (label, ea)
                (OUT / (stem + ".asm")).write_text("\n".join(asm) + "\n")
                if ida_hexrays.init_hexrays_plugin():
                    try:
                        cf = ida_hexrays.decompile(ea)
                        item["decompiled"] = cf is not None
                        if cf is not None:
                            (OUT / (stem + ".c")).write_text(str(cf) + "\n")
                    except Exception as exc:
                        item["decompiler_error"] = str(exc)
            result["functions"].append(item)
        refs = {}
        for ea in (0x104C948F0, 0x1069D1140, 0x1069D1150,
                   0x104D121EC, 0x104D11BC4, 0x104D126C8, 0x104C88820):
            f = ida_funcs.get_func(ea)
            refs[hex(ea)] = {
                "name": idc.get_name(ea),
                "existing_containing_function": hex(f.start_ea) if f else None,
                "disassembly": ida_lines.tag_remove(idc.generate_disasm_line(ea, 0) or ""),
                "code_refs_to": [hex(x) for x in idautils.CodeRefsTo(ea, False)],
                "data_refs_to": [hex(x) for x in idautils.DataRefsTo(ea)],
                "all_xrefs_to": [{"frm": hex(x.frm), "type": x.type,
                                  "instruction": ida_lines.tag_remove(idc.generate_disasm_line(x.frm, 0) or ""),
                                  "containing_function": (hex(ida_funcs.get_func(x.frm).start_ea)
                                                          if ida_funcs.get_func(x.frm) else None)}
                                 for x in idautils.XrefsTo(ea, 0)]}
        result["bounded_reference_queries"] = refs
        (OUT / "ida-source-producer-manifest.json").write_text(json.dumps(result, indent=2) + "\n")
        progress_path.write_text("exact-bound targeted export complete\n")
        print("exact-bound source producer export complete", flush=True)
        return
    if "--fast" in idc.ARGV:
        fast = {ea: label for ea, label in TARGETS.items()
                if ea in {0x1048EDE18, 0x1048EE24C, 0x1048EE1B4, 0x1048E1A74,
                          0x104CC0320, 0x104CC31B8,
                          0x1044C5FFC, 0x1044C5FD0, 0x102114B48, 0x102114E8C, 0x1021D6054,
                          0x102156CD0, 0x1021572FC, 0x1021746EC, 0x104D4AB60, 0x104D4AD24,
                          0x102139588, 0x102139ECC, 0x102139ED8,
                          0x104F233B0, 0x104F2350C,
                          0x104DA616C, 0x104DAA598, 0x1015FA0D8,
                          0x104F21168, 0x1044C5768, 0x1044C67F0, 0x1044C6ACC,
                          0x104C8B8DC, 0x1021168A4, 0x102116D3C, 0x10211706C,
                          0x104DB2FEC, 0x104C9E1C0, 0x104E247D0,
                          0x104DD6080, 0x104D17A78,
                          0x104DD5CF4, 0x104DB28B8, 0x104D8DFEC,
                          0x104D8B064, 0x104D8B1AC, 0x104DFE02C,
                          0x104D22EC4,
                          0x104D4CDD0, 0x104CC2F4C, 0x104CC2EE8, 0x104CC38C4,
                          0x104DB2C60, 0x104DB2C40, 0x102C0A078, 0x1011FFFD8,
                          0x1017341D0, 0x100F15D38, 0x104D4BF90, 0x104D4BCDC,
                          0x102C0C0F0, 0x102C92AE8, 0x102C9271C, 0x102C923EC,
                          0x102137EF8, 0x102160DA8, 0x1021C71D4, 0x1022285E4,
                          0x102229B80, 0x104C93720, 0x104C8E3E8, 0x102ABB48C,
                          0x104EEE9E4,
                          0x1015FC948, 0x1015FCAA0,
                          0x1015EF460,
                          0x103AEA748, 0x103EB93E4, 0x104DBA8CC,
                          0x104D4B8C0, 0x104D4C034, 0x104D4C0A0,
                          0x104D4C650, 0x104DB2FEC, 0x104C9E1C0, 0x104E247D0,
                          0x104D4C9C8, 0x104DBE5F4, 0x104DC0EFC, 0x104DC0F00, 0x1015F9230,
                          0x102C0C59C, 0x102C0C64C, 0x102C0C710,
                          0x102C0C914,
                          0x102C0C7F0,
                          0x104C9D9CC,
                          0x104C9E150,
                          0x104C92A80, 0x104C94940, 0x104C8B21C, 0x104C98F30,
                          0x104C8B8DC, 0x104C9E348,
                          0x1044C58D0}}
        input_path = ida_nalt.get_input_file_path()
        pinned = Path("/tmp/roblox-physics-analysis-arm64")
        result = {"database": ida_loader.get_path(ida_loader.PATH_TYPE_IDB),
                  "input_path": input_path,
                  "pinned_binary_sha256": hashlib.sha256(pinned.read_bytes()).hexdigest() if pinned.exists() else None,
                  "static_only": True, "database_saved": False, "functions": [],
                  "table_bytes": {"address": hex(0x105EA81A0),
                                  "get_bytes_hex": (ida_bytes.get_bytes(0x105EA81A0, 20) or b"").hex(),
                                  "get_wide_byte": [ida_bytes.get_wide_byte(0x105EA81A0 + i) for i in range(20)]}}
        result["part_vtable_candidates"] = []
        result["physics_byte_remap"] = {
            "address": hex(0x105EAB5B1),
            "bytes_0_to_255_hex": (ida_bytes.get_bytes(0x105EAB5B1, 256) or b"").hex(),
            "guard_global_bytes": {hex(ea): ida_bytes.get_wide_byte(ea)
                                   for ea in (0x106A47FD0, 0x106A48000)},
            "xrefs": {hex(ea): [hex(x) for x in idautils.DataRefsTo(ea)]
                      for ea in (0x105EAB5B1, 0x106A47FD0, 0x106A48000)}}
        ts_refs = list(idautils.DataRefsTo(0x106D1F138))
        result["ts_global_clock_refs"] = {
            "address": hex(0x106D1F138),
            "data_refs": [hex(x) for x in ts_refs],
            "caller_refs": [hex(x) for x in idautils.CodeRefsTo(0x102C0A078, False)]}
        if "--layout-audit" in idc.ARGV:
            target = 0x104C9D9CC
            xrefs = list(idautils.XrefsTo(target, 0))
            result["replicator_ctor_reference_candidate_xrefs"] = {
                "target": hex(target),
                "code_refs_to": [hex(x) for x in idautils.CodeRefsTo(target, False)],
                "data_refs_to": [hex(x) for x in idautils.DataRefsTo(target)],
                "all_xrefs_to": [{"frm": hex(x.frm), "to": hex(x.to), "type": x.type,
                                  "instruction": ida_lines.tag_remove(idc.generate_disasm_line(x.frm, 0) or ""),
                                  "containing_function": (hex(ida_funcs.get_func(x.frm).start_ea)
                                                          if ida_funcs.get_func(x.frm) else None)}
                                 for x in xrefs]}
        if "--layout-audit" in idc.ARGV:
            stores = []
            # Scan native text for direct stores to the TS object's +160 field
            # and adjacent pair-store encodings that could cover it.
            for fstart in idautils.Functions():
                for ea in idautils.FuncItems(fstart):
                    insn = ida_ua.insn_t()
                    if ida_ua.decode_insn(insn, ea) <= 0:
                        continue
                    mnem = (idc.print_insn_mnem(ea) or "").lower()
                    if mnem not in ("str", "stur", "stp", "stnp", "strb", "strh", "strq"):
                        continue
                    for op in insn.ops:
                        if op.type == ida_ua.o_displ and op.addr in (0xA0, 0x98, 0x90):
                            stores.append({"ea": hex(ea), "function": hex(fstart),
                                           "asm": ida_lines.tag_remove(idc.generate_disasm_line(ea, 0) or "")})
                            break
            result["store_imm_offset_160_candidates"] = stores
        for address, label in ((0x10678A688, "Part_vtable_candidate_a"),
                               (0x106990160, "Part_vtable_candidate_b"),
                               (0x1067B50A8, "ClassDescriptor_vtable_candidate")):
            words = [ida_bytes.get_qword(address + 8 * i) for i in range(24)]
            result["part_vtable_candidates"].append({
                "address": hex(address), "label": label,
                "words": [{"value": hex(value), "name": idc.get_name(value),
                           "function_start": (hex(ida_funcs.get_func(value).start_ea)
                                              if ida_funcs.get_func(value) else None)}
                          for value in words],
                "data_xrefs_to": [hex(x) for x in idautils.DataRefsTo(address)]})
        for ea, label in fast.items():
            item = export(ea, label, "explicit")
            result["functions"].append(item)
            progress_path.write_text("fast-exported %d/%d; last=%x\n" % (len(result["functions"]), len(fast), ea))
        if "--layout-audit" in idc.ARGV:
            for ea, label in ((0x104D4B8C0, "flush_caller_a_containing_function"),
                              (0x104D4C034, "flush_caller_b_containing_function"),
                              (0x104D4C0A0, "flush_caller_c_containing_function")):
                f = ida_funcs.get_func(ea)
                if f:
                    result["functions"].append(export(f.start_ea, label, "containing_flush_caller"))
                else:
                    result["functions"].append({"address": hex(ea), "label": label,
                                                "error": "no containing function"})
        result["class_default_string_xrefs"] = []
        extra_targets = {}
        for ea, label in CLASS_DEFAULT_STRING_ADDRESSES.items():
            refs = list(idautils.DataRefsTo(ea))
            entry = {"address": hex(ea), "label": label,
                     "references": [{"ea": hex(ref),
                                     "function": (hex(ida_funcs.get_func(ref).start_ea)
                                                  if ida_funcs.get_func(ref) else None)}
                                    for ref in refs]}
            result["class_default_string_xrefs"].append(entry)
            for ref in refs:
                f = ida_funcs.get_func(ref)
                if f and f.start_ea not in fast and f.start_ea not in extra_targets:
                    extra_targets[f.start_ea] = "class_default_ref_%x" % f.start_ea
        result["functions"].extend(export(ea, label, "class_default_string_ref")
                                   for ea, label in sorted(extra_targets.items()))
        (OUT / "ida-fast-manifest.json").write_text(json.dumps(result, indent=2) + "\n")
        print("static targeted fast export: %d functions" % len(result["functions"]), flush=True)
        return
    pinned = Path("/tmp/roblox-physics-analysis-arm64")
    pinned_sha256 = hashlib.sha256(pinned.read_bytes()).hexdigest() if pinned.exists() else None
    try:
        input_path = ida_loader.get_input_file_path()
    except Exception:
        input_path = None
    manifest = {"database": ida_loader.get_path(ida_loader.PATH_TYPE_IDB),
                "input_path": input_path, "pinned_binary_sha256": pinned_sha256,
                "static_only": True, "database_saved": False,
                "functions": [], "string_xrefs": []}
    targets = dict(TARGETS)
    # Export explicit functions first so valuable evidence survives even if a
    # later targeted RTTI/string lookup takes a long time.
    for ea, label in sorted(targets.items()):
        manifest["functions"].append(export(ea, label, "explicit"))
        progress_path.write_text("exported %d/%d; last=%x\n" %
                                 (len(manifest["functions"]), len(targets), ea))
        (OUT / "ida-manifest.partial.json").write_text(json.dumps(manifest, indent=2) + "\n")
    # Query only relevant fixed string addresses from the saved string index.
    for ea, value in STRING_ADDRESSES.items():
        refs = list(idautils.DataRefsTo(ea))
        hit = {"address": hex(ea), "text": value, "references": []}
        for ref in refs:
            f = ida_funcs.get_func(ref)
            hit["references"].append({"ea": hex(ref), "function": hex(f.start_ea) if f else None})
            if f and f.start_ea not in targets:
                targets[f.start_ea] = "string_ref_%x" % f.start_ea
        manifest["string_xrefs"].append(hit)
    for ea, label in sorted(targets.items()):
        if ea not in TARGETS:
            manifest["functions"].append(export(ea, label, "string_xref"))
    tables = []
    for address, size, label in (
        (0x105EA8130, 56, "send_type_first_u32"),
        (0x105EA8168, 56, "send_type_second_u32"),
        (0x105EA81A0, 14, "send_type_byte"),
        (0x1044C5FD0, 14 * 8, "channel_open_parameter_pairs"),
    ):
        raw = ida_bytes.get_bytes(address, size)
        tables.append({"address": hex(address), "size": size, "label": label,
                       "bytes_hex": raw.hex() if raw else None,
                       "patch_verification": "unavailable in this IDA Python API",
                       "data_xrefs_to": [hex(x) for x in idautils.DataRefsTo(address)],
                       "code_xrefs_to": [hex(x) for x in idautils.CodeRefsTo(address, False)]})
    manifest["tables"] = tables
    (OUT / "ida-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("static targeted export: %d functions, %d matching strings" %
          (len(manifest["functions"]), len(manifest["string_xrefs"])), flush=True)


try:
    main()
except BaseException:
    traceback.print_exc()
    ida_pro.qexit(1)
ida_pro.qexit(0)
