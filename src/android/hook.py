def after_apk_build(toolchain):
    manifest_fn = "{}/src/main/AndroidManifest.xml".format(
        toolchain._dist.dist_dir)
    with open(manifest_fn, "r", encoding="utf-8") as fd:
        manifest = fd.read()

    PROVIDER = """
    <provider
        android:name="androidx.core.content.FileProvider"
        android:authorities="${applicationId}.fileprovider"
        android:exported="false"
        android:grantUriPermissions="true">
        <meta-data
            android:name="android.support.FILE_PROVIDER_PATHS"
            android:resource="@xml/file_paths" />
    </provider>
    """

    if "androidx.core.content.FileProvider" not in manifest:
        manifest = manifest.replace("</application>", PROVIDER + "</application>")
        with open(manifest_fn, "w", encoding="utf-8") as fd:
            fd.write(manifest)
