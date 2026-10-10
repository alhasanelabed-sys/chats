package android.content;

import java.io.File;

/** JVM-only seam for app-private persistence tests; never packaged in the APK. */
public abstract class Context {
    public abstract File getFilesDir();
}
