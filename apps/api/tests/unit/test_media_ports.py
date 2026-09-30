from sentrix.domain.media import ports


def test_ports_are_protocols_not_concrete_classes():
    # ``VlmObserver`` 与 ``ObservationSemantics`` 比这份名单先落地，一直没被列进来；
    # 本切片新加的 ``PhotoInspector``（T123/T124 共用的上游件）随手补上——
    # 这条不变量问的是「端口有没有写成 Protocol」，新端口就该当场被它盖住。
    for name in (
        "AssetRepository",
        "MediaStorage",
        "MetadataReader",
        "PreviewEncoder",
        "PhotoInspector",
    ):
        port = getattr(ports, name)
        assert getattr(port, "_is_protocol", False), f"{name} 必须是 Protocol"


def test_supported_image_suffixes_are_declared_in_domain():
    """支持哪些格式是领域规则，不是适配器细节。"""
    assert ".heic" in ports.SUPPORTED_IMAGE_SUFFIXES
    assert ".jpg" in ports.SUPPORTED_IMAGE_SUFFIXES


def test_heic_needs_transcode_rule_lives_in_domain():
    assert ports.needs_transcode("a.heic", "image/heic") is True
    assert ports.needs_transcode("a.jpg", "image/jpeg") is False
