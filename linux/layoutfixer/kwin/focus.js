// Tells LayoutFixer which window has focus. On Wayland only the compositor knows this.
// It sends the window's app identifiers and nothing else.
function report(window) {
    callDBus(
        "io.github.layoutfixer",
        "/Focus",
        "io.github.layoutfixer.Focus",
        "changed",
        window ? String(window.resourceClass) : "",
        window ? String(window.desktopFileName) : ""
    );
}

workspace.windowActivated.connect(report);
report(workspace.activeWindow);
