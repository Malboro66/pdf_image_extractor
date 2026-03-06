import importlib

from pdf_image_extractor.core import pipeline


def test_pipeline_module_importable_without_nameerror() -> None:
    module = importlib.reload(pipeline)

    assert module is not None
    assert hasattr(module, "_MP_CONTEXT_LOCK")
