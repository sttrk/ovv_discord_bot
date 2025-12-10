# ovv/bis/interface_box.py （抜粋・下部のみ差し替え）

    # 7. Discord 返信内容の抽出
    message_for_user = _extract_message_for_user(core_result)

    # 8. Stabilizer の構築
    stabilizer = Stabilizer(
        message_for_user=message_for_user,
        notion_ops=notion_ops,
        context_key=packet.get("context_key"),
        user_id=str(packet.get("user_id") or ""),
        task_id=str(packet.get("task_id") or None),
    )

    # 9. 上位（Boundary_Gate）に返却するペイロード
    return {
        "packet": packet,
        "core_result": core_result,
        "notion_ops": notion_ops,
        "state": state,
        "stabilizer": stabilizer,
        "trace": {
            "iface": "interface_box",
            "pipeline": "build_pipeline",
            "notion_ops_built": notion_ops is not None,
        },
    }