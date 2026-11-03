import AppKit
import WebKit

final class RoveApp: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate {
    private var windows: [NSWindow] = []
    private var backend: Process?
    private var initialURL: URL?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.appearance = NSAppearance(named: .darkAqua)
        let menu = NSMenu()
        let item = NSMenuItem(); menu.addItem(item)
        let applicationMenu = NSMenu(); item.submenu = applicationMenu
        applicationMenu.addItem(withTitle: "About Rove", action: #selector(about), keyEquivalent: "")
        applicationMenu.addItem(.separator())
        applicationMenu.addItem(withTitle: "New Window", action: #selector(newWindow), keyEquivalent: "n")
        applicationMenu.addItem(withTitle: "Reload", action: #selector(reload), keyEquivalent: "r")
        applicationMenu.addItem(.separator())
        applicationMenu.addItem(withTitle: "Hide Rove", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        applicationMenu.addItem(withTitle: "Quit Rove", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let edit = NSMenuItem(); menu.addItem(edit); let editMenu=NSMenu(title:"Edit");edit.submenu=editMenu
        editMenu.addItem(withTitle:"Undo",action:Selector(("undo:")),keyEquivalent:"z")
        editMenu.addItem(withTitle:"Cut",action:#selector(NSText.cut(_:)),keyEquivalent:"x")
        editMenu.addItem(withTitle:"Copy",action:#selector(NSText.copy(_:)),keyEquivalent:"c")
        editMenu.addItem(withTitle:"Paste",action:#selector(NSText.paste(_:)),keyEquivalent:"v")
        editMenu.addItem(withTitle:"Select All",action:#selector(NSText.selectAll(_:)),keyEquivalent:"a")
        NSApp.mainMenu=menu
        guard let root=Bundle.main.object(forInfoDictionaryKey:"RoveProjectRoot") as? String else {fail("Missing project path");return}
        let process=Process(),output=Pipe(),error=Pipe()
        process.executableURL=URL(fileURLWithPath:root+"/.venv/bin/python")
        process.arguments=[root+"/scripts/desktop_backend.py"]
        process.currentDirectoryURL=URL(fileURLWithPath:root)
        process.standardOutput=output;process.standardError=error
        process.terminationHandler={ [weak self] job in
            let text=String(data:output.fileHandleForReading.readDataToEndOfFile(),encoding:.utf8)?.trimmingCharacters(in:.whitespacesAndNewlines) ?? ""
            DispatchQueue.main.async {
                guard job.terminationStatus==0,let url=URL(string:text),url.scheme=="http",url.host=="127.0.0.1" else {self?.fail("The local backend could not start. Open outputs/desktop/backend.log for details.");return}
                self?.initialURL=url;self?.makeWindow(url:url)
            }
        }
        backend=process
        do {try process.run()} catch {fail(error.localizedDescription)}
    }

    @discardableResult private func makeWindow(url:URL,configuration:WKWebViewConfiguration?=nil) -> WKWebView {
        let config=configuration ?? WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        config.preferences.isElementFullscreenEnabled=true
        let web=WKWebView(frame:.zero,configuration:config)
        web.navigationDelegate=self;web.uiDelegate=self
        web.allowsBackForwardNavigationGestures=true
        let window=NSWindow(contentRect:NSRect(x:0,y:0,width:1320,height:860),styleMask:[.titled,.closable,.miniaturizable,.resizable],backing:.buffered,defer:false)
        window.title="Rove";window.minSize=NSSize(width:760,height:540)
        window.contentView=web;window.center();window.isReleasedWhenClosed=false
        window.collectionBehavior.insert(.fullScreenPrimary)
        windows.append(window);window.makeKeyAndOrderFront(nil);NSApp.activate(ignoringOtherApps:true)
        web.load(URLRequest(url:url));return web
    }
    func webView(_ webView:WKWebView,decidePolicyFor action:WKNavigationAction,decisionHandler:@escaping(WKNavigationActionPolicy)->Void) {
        guard let url=action.request.url else {decisionHandler(.cancel);return}
        if url.scheme=="http" && url.host=="127.0.0.1" {decisionHandler(.allow)}
        else {decisionHandler(.cancel);if url.scheme=="https" {NSWorkspace.shared.open(url)}}
    }
    func webView(_ webView:WKWebView,createWebViewWith configuration:WKWebViewConfiguration,for action:WKNavigationAction,windowFeatures:WKWindowFeatures)->WKWebView? {
        guard let url=action.request.url,url.scheme=="http",url.host=="127.0.0.1" else {if let url=action.request.url,url.scheme=="https" {NSWorkspace.shared.open(url)};return nil}
        return makeWindow(url:url,configuration:configuration)
    }
    func webView(_ webView:WKWebView,didFinish navigation:WKNavigation!) {webView.window?.title=webView.title ?? "Rove"}
    func applicationShouldTerminateAfterLastWindowClosed(_ sender:NSApplication)->Bool {true}
    // Closing this app only detaches its display. The shared backend and any
    // terminal game keep running until the explicit Leave action is used.
    @objc private func newWindow(){if let url=initialURL {makeWindow(url:url)}}
    @objc private func reload(){(NSApp.keyWindow?.contentView as? WKWebView)?.reload()}
    @objc private func about(){let a=NSAlert();a.messageText="Rove";a.informativeText="Local browser and macOS client for your shared Luau session. Live rendering is under development.";a.runModal()}
    private func fail(_ message:String){let a=NSAlert();a.messageText="Rove could not open";a.informativeText=message;a.runModal();NSApp.terminate(nil)}
}
let application=NSApplication.shared
let delegate=RoveApp()
application.setActivationPolicy(.regular)
application.delegate=delegate
application.run()
