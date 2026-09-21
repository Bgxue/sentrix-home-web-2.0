from sentrix.domain.media import ports


def test_ports_are_protocols_not_concrete_classes():
    for name in ("AssetRepository", "MediaStorage", "MetadataReader", "PreviewEncoder"):
        port = getattr(ports, name)
        assert getattr(port, "_is_protocol", False), f"{name} 必须是 Protocol"


def test_supported_image_suffixes_are_declared_in_domain():
    """支持哪些格式是领域规则，不是适配器细节。"""
    assert ".heic" in ports.SUPPORTED_IMAGE_SUFFIXES
    assert ".jpg" in ports.SUPPORTED_IMAGE_SUFFIXES


def test_heic_needs_transcode_rule_lives_in_domain():
    assert ports.needs_transcode("a.heic", "image/heic") is True
    assert ports.needs_transcode("a.jpg", "image/jpeg") is False
