import Cocoa
import FlutterMacOS

class MainFlutterWindow: NSWindow {
  override func awakeFromNib() {
    let flutterViewController = FlutterViewController()
    self.contentViewController = flutterViewController

    // Match the panel: 1024×600 logical px, fixed size. On the Pi, flutter-pi
    // runs with --pixel-ratio 1.0, so both lay out on the same canvas.
    let panelSize = NSSize(width: 1024, height: 600)
    self.styleMask.remove(.resizable)
    self.setContentSize(panelSize)
    self.contentMinSize = panelSize
    self.contentMaxSize = panelSize
    self.center()

    RegisterGeneratedPlugins(registry: flutterViewController)

    super.awakeFromNib()
  }
}
