def test_package_imports_and_has_version():
    import fretsure

    assert isinstance(fretsure.__version__, str)
    assert fretsure.__version__ == "0.5.0"
