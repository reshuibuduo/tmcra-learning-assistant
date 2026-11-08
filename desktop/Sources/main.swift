import AppKit
import WebKit
import AVFoundation

private let accent = NSColor(calibratedRed: 0.34, green: 0.40, blue: 0.84, alpha: 1)
private func label(_ text: String, size: CGFloat = 12, weight: NSFont.Weight = .regular, color: NSColor = .labelColor) -> NSTextField {
    let view = NSTextField(wrappingLabelWithString: text)
    view.font = .systemFont(ofSize: size, weight: weight)
    view.textColor = color
    view.isSelectable = false
    return view
}
private func stack(_ views: [NSView], vertical: Bool = false, spacing: CGFloat = 10) -> NSStackView {
    let result = NSStackView(views: views)
    result.orientation = vertical ? .vertical : .horizontal
    result.alignment = vertical ? .leading : .centerY
    result.spacing = spacing
    return result
}
private func spacer() -> NSView {
    let v = NSView()
    v.setContentHuggingPriority(.defaultLow, for: .horizontal)
    return v
}

final class CompanionPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

final class DesktopApp: NSObject, NSApplicationDelegate, NSWindowDelegate, WKScriptMessageHandler, WKNavigationDelegate, WKUIDelegate {
    var mainWindow: NSWindow!
    var panel: CompanionPanel!
    var webView: WKWebView!
    var statusItem: NSStatusItem!
    var state = PanelState()
    var isDemo = false
    var isBundledPage = true
    var configuredURL = WorkbenchAddress.defaultURL
    var hintsVisible = true
    var nativeTimer: Timer?
    var activity: NSObjectProtocol?
    var commandPending = false
    var lastBridgeUpdate = Date()
    var serverHealthy: Bool? = nil
    var healthInFlight = false
    var pollCount = 0
    var closing = false
    let badge = label("未连接", size: 10, color: .secondaryLabelColor)
    let status = label("尚未连接学习服务", size: 11, color: .secondaryLabelColor)
    let duration = label("00:00", size: 13, weight: .medium, color: accent)
    let cardTitle = label("等待下一次理解", size: 17, weight: .semibold)
    let cardBody = label("连接学习服务后，新的解释会出现在这里。", size: 13, color: .secondaryLabelColor)
    let cardExample = label("", size: 12, color: .secondaryLabelColor)
    let footer = label("主窗口最小化后，浮窗仍可操作", size: 10, color: .secondaryLabelColor)
    var cardStack: NSStackView!
    var recordButton: NSButton!
    var hintButton: NSButton!
    var toolbarStatus = label("服务未连接", size: 11, color: .secondaryLabelColor)
    var menuHints: NSMenuItem!

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.appearance = NSAppearance(named: .aqua)
        if let saved = UserDefaults.standard.string(forKey: "workbenchURL"), let url = WorkbenchAddress.validate(saved) { configuredURL = url }
        if UserDefaults.standard.object(forKey: "showHints") != nil { hintsVisible = UserDefaults.standard.bool(forKey: "showHints") }
        buildMenus()
        buildMainWindow()
        buildPanel()
        loadBundled("offline")
        mainWindow.makeKeyAndOrderFront(nil)
        panel.orderFrontRegardless()
        NSApp.activate(ignoringOtherApps: true)
        nativeTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.pollPage() }
        RunLoop.main.add(nativeTimer!, forMode: .common)
    }

    func makeButton(_ title: String, _ action: Selector, symbol: String? = nil) -> NSButton {
        let b = NSButton(title: title, target: self, action: action)
        b.bezelStyle = .rounded
        b.font = .systemFont(ofSize: 12, weight: .medium)
        if let symbol { b.image = NSImage(systemSymbolName: symbol, accessibilityDescription: nil); b.imagePosition = title.isEmpty ? .imageOnly : .imageLeading }
        return b
    }

    func buildMenus() {
        let mainMenu = NSMenu()
        let appRoot = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "显示主窗口", action: #selector(showMain), keyEquivalent: "1").target = self
        appMenu.addItem(withTitle: "显示悬浮窗", action: #selector(showPanel), keyEquivalent: "2").target = self
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "退出随堂助手", action: #selector(quitApp), keyEquivalent: "q").target = self
        appRoot.submenu = appMenu; mainMenu.addItem(appRoot)
        let editRoot = NSMenuItem(title: "编辑", action: nil, keyEquivalent: "")
        let editMenu = NSMenu(title: "编辑")
        for (title, action, key) in [("剪切", "cut:", "x"),("拷贝", "copy:", "c"),("粘贴", "paste:", "v"),("全选", "selectAll:", "a")] {
            editMenu.addItem(withTitle: title, action: Selector(action), keyEquivalent: key)
        }
        editRoot.submenu = editMenu; mainMenu.addItem(editRoot); NSApp.mainMenu = mainMenu
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.image = NSImage(systemSymbolName: "waveform.circle", accessibilityDescription: "TMCRA 随堂助手")
        statusItem.button?.toolTip = "TMCRA 随堂助手 · 悬浮窗控制"
        let menu = NSMenu()
        menu.addItem(withTitle: "显示悬浮窗", action: #selector(showPanel), keyEquivalent: "").target = self
        menu.addItem(withTitle: "收起悬浮窗", action: #selector(hidePanel), keyEquivalent: "").target = self
        menu.addItem(withTitle: "返回主窗口", action: #selector(showMain), keyEquivalent: "").target = self
        menu.addItem(.separator())
        menu.addItem(withTitle: "开始／停止采集", action: #selector(toggleCapture), keyEquivalent: "").target = self
        menuHints = menu.addItem(withTitle: "显示解释提示", action: #selector(toggleHints), keyEquivalent: ""); menuHints.target = self
        menu.addItem(.separator())
        menu.addItem(withTitle: "退出随堂助手", action: #selector(quitApp), keyEquivalent: "").target = self
        statusItem.menu = menu
    }

    func buildMainWindow() {
        let config = WKWebViewConfiguration()
        let content = WKUserContentController()
        content.add(self, name: "desktop")
        let bridgeURL = Bundle.main.url(forResource: "bridge", withExtension: "js")!
        let bridge = try! String(contentsOf: bridgeURL, encoding: .utf8)
        content.addUserScript(WKUserScript(source: bridge, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        config.userContentController = content
        config.websiteDataStore = .nonPersistent()
        webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self; webView.uiDelegate = self
        webView.setAccessibilityLabel("TMCRA 学习工作台")
        mainWindow = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1060, height: 730), styleMask: [.titled,.closable,.miniaturizable,.resizable], backing: .buffered, defer: false)
        mainWindow.title = "TMCRA 随堂助手"
        mainWindow.minSize = NSSize(width: 760, height: 560)
        mainWindow.delegate = self; mainWindow.isReleasedWhenClosed = false
        mainWindow.center(); mainWindow.setFrameAutosaveName("TMCRA.Companion.Main")
        let toolbar = stack([label("TMCRA  /  随堂助手", size: 13, weight: .semibold),spacer(),toolbarStatus,
            makeButton("连接服务",#selector(connectDialog),symbol:"link"),
            makeButton("离线演示",#selector(openDemo)),
            makeButton("最小化到浮窗",#selector(minimizeMain),symbol:"pip.enter")])
        toolbar.edgeInsets = NSEdgeInsets(top: 10,left: 16,bottom: 10,right: 16)
        let root = NSView(); mainWindow.contentView = root
        for view in [toolbar,webView!] { view.translatesAutoresizingMaskIntoConstraints = false; root.addSubview(view) }
        NSLayoutConstraint.activate([toolbar.topAnchor.constraint(equalTo: root.topAnchor),toolbar.leadingAnchor.constraint(equalTo: root.leadingAnchor),toolbar.trailingAnchor.constraint(equalTo: root.trailingAnchor),toolbar.heightAnchor.constraint(equalToConstant: 48),webView.topAnchor.constraint(equalTo: toolbar.bottomAnchor),webView.leadingAnchor.constraint(equalTo: root.leadingAnchor),webView.trailingAnchor.constraint(equalTo: root.trailingAnchor),webView.bottomAnchor.constraint(equalTo: root.bottomAnchor)])
    }

    func buildPanel() {
        panel = CompanionPanel(contentRect: NSRect(x: 0,y: 0,width: 370,height: 315),styleMask: [.borderless,.nonactivatingPanel],backing: .buffered,defer: false)
        panel.title = "TMCRA 悬浮窗"
        panel.level = .floating
        panel.hidesOnDeactivate = false
        panel.isFloatingPanel = true
        panel.becomesKeyOnlyIfNeeded = true
        panel.worksWhenModal = true
        panel.isMovableByWindowBackground = true
        panel.collectionBehavior = [.canJoinAllSpaces,.fullScreenAuxiliary,.ignoresCycle]
        panel.isReleasedWhenClosed = false
        panel.hasShadow = true
        panel.backgroundColor = .clear; panel.isOpaque = false
        panel.setFrameAutosaveName("TMCRA.Companion.Floating")
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x:0,y:0,width:1440,height:900)
        panel.setFrameOrigin(NSPoint(x:screen.maxX-390,y:screen.maxY-335))
        let root = NSView();root.wantsLayer = true;root.layer?.cornerRadius = 16
        // The app uses Aqua; resolve a matching opaque fill independently of the system theme.
        root.layer?.backgroundColor = NSColor(calibratedRed: 0.98, green: 0.985, blue: 1.0, alpha: 1).cgColor
        root.layer?.borderWidth = 1;root.layer?.borderColor = NSColor(calibratedWhite: 0.84,alpha: 0.7).cgColor
        panel.contentView = root
        let icon = NSImageView(image:NSImage(systemSymbolName:"sparkles",accessibilityDescription:nil)!)
        icon.contentTintColor = accent
        let close = makeButton("",#selector(hidePanel),symbol:"xmark")
        close.isBordered = false;close.setAccessibilityLabel("收起悬浮窗");close.toolTip = "收起浮窗，录音不受影响；可从菜单栏恢复"
        let header = stack([icon,label("随堂助手",size:13,weight:.semibold),badge,spacer(),close])
        recordButton = makeButton("开始录音",#selector(toggleCapture),symbol:"mic.fill")
        recordButton.contentTintColor = accent; recordButton.setAccessibilityIdentifier("floatingRecord")
        hintButton = makeButton("提示开启",#selector(toggleHints),symbol:"eye")
        hintButton.setAccessibilityIdentifier("floatingHints")
        let restore = makeButton("",#selector(showMain),symbol:"macwindow")
        restore.setAccessibilityLabel("返回主窗口");restore.toolTip = "返回主窗口"
        let controls = stack([recordButton,hintButton,spacer(),restore],spacing:8)
        let meta = stack([status,spacer(),duration],spacing:8)
        cardTitle.maximumNumberOfLines = 2
        cardBody.maximumNumberOfLines = 5;cardExample.maximumNumberOfLines = 3
        cardStack = stack([cardTitle,cardBody,cardExample],vertical:true,spacing:9)
        for v in [cardTitle,cardBody,cardExample] { v.setContentCompressionResistancePriority(.defaultLow,for:.vertical) }
        let line=NSBox();line.boxType = .separator
        let outer = stack([header,controls,meta,line,cardStack,footer],vertical:true,spacing:12)
        outer.translatesAutoresizingMaskIntoConstraints = false;root.addSubview(outer)
        NSLayoutConstraint.activate([outer.leadingAnchor.constraint(equalTo:root.leadingAnchor,constant:18),outer.trailingAnchor.constraint(equalTo:root.trailingAnchor,constant:-18),outer.topAnchor.constraint(equalTo:root.topAnchor,constant:14),outer.bottomAnchor.constraint(lessThanOrEqualTo:root.bottomAnchor,constant:-14),icon.widthAnchor.constraint(equalToConstant:18),icon.heightAnchor.constraint(equalToConstant:18)])
        for v in [header,controls,meta,line,cardStack!] { v.widthAnchor.constraint(equalTo:outer.widthAnchor).isActive = true }
        for v in [cardTitle,cardBody,cardExample] { v.widthAnchor.constraint(equalTo:cardStack.widthAnchor).isActive = true }
        render()
    }

    func resource(_ name: String) -> URL { Bundle.main.url(forResource:name,withExtension:"html")! }
    func loadBundled(_ name: String) {
        isDemo = name == "demo";isBundledPage = true;commandPending = false
        serverHealthy = nil
        state = PanelState();state.demo = isDemo;state.status = isDemo ? "离线演示 · 没有使用麦克风" : "尚未连接学习服务"
        render();webView.loadFileURL(resource(name),allowingReadAccessTo:Bundle.main.resourceURL!)
    }
    @objc func openDemo() {
        guard !state.captureBusy else { alert("请先停止当前采集，再切换到演示。");return }
        loadBundled("demo");showPanel()
    }
    @objc func connectDialog() {
        guard !state.captureBusy else { alert("请先停止当前采集，再切换服务。");return }
        showMain()
        let dialog=NSAlert();dialog.messageText="连接学习工作台";dialog.informativeText="使用本机 SSH 转发地址。也可以粘贴一次性登录链接；链接中的登录码不会被保存。"
        dialog.addButton(withTitle:"连接");dialog.addButton(withTitle:"取消")
        let field=NSTextField(frame:NSRect(x:0,y:0,width:440,height:26));field.stringValue=configuredURL.absoluteString;field.placeholderString="http://127.0.0.1:18782/";dialog.accessoryView=field
        guard dialog.runModal() == .alertFirstButtonReturn else {return}
        guard let url=WorkbenchAddress.validate(field.stringValue) else {alert("请输入 localhost 或 127.0.0.1 的工作台根地址。");return}
        configuredURL=WorkbenchAddress.withoutSecret(url);UserDefaults.standard.set(configuredURL.absoluteString,forKey:"workbenchURL")
        isDemo=false;isBundledPage=false;serverHealthy=nil;state=PanelState();state.status="正在连接学习服务…";render()
        webView.load(URLRequest(url:url,cachePolicy:.reloadIgnoringLocalCacheData,timeoutInterval:8))
    }
    func render() {
        guard panel != nil else {return}
        badge.stringValue = isDemo ? "离线演示" : state.connected ? "已连接" : "未连接"
        status.stringValue=state.status;duration.stringValue=state.duration
        toolbarStatus.stringValue = isDemo ? "演示 · 不采集音频" : state.connected ? "学习服务已连接" : "学习服务未连接"
        recordButton.title = commandPending ? "正在处理…" : state.captureBusy ? (isDemo ? "停止模拟" : "停止录音") : (isDemo ? "开始模拟" : "开始录音")
        recordButton.image=NSImage(systemSymbolName:state.captureBusy ? "stop.fill" : "mic.fill",accessibilityDescription:nil)
        recordButton.contentTintColor=state.captureBusy ? .systemRed : accent
        recordButton.isEnabled = !commandPending && (state.captureBusy ? state.canStop : state.canStart)
        hintButton.title = hintsVisible ? "提示开启" : "提示关闭"
        hintButton.image=NSImage(systemSymbolName:hintsVisible ? "eye" : "eye.slash",accessibilityDescription:nil)
        menuHints.state = hintsVisible ? .on : .off
        cardStack.isHidden = !hintsVisible
        cardTitle.stringValue = state.card.isEmpty ? "等待下一次理解" : (isDemo ? "演示 · " : "")+state.card.title
        cardBody.stringValue = state.card.isEmpty ? "连接学习服务后，新的解释会出现在这里。" : state.card.explanation
        cardExample.stringValue = state.card.example
        cardExample.isHidden = state.card.example.isEmpty
        footer.stringValue = isDemo ? "演示数据 · 不使用麦克风或模型" : !hintsVisible ? "提示已隐藏，录音开关独立控制" : state.card.kind == "learning_memory" ? "联系以前的学习 · 可回主窗口查看来源" : "通用知识解释 · 可回主窗口核对原句"
        let height:CGFloat = hintsVisible ? 335 : 170
        if abs(panel.frame.height-height)>1 { var f=panel.frame;f.origin.y += f.height-height;f.size.height=height;panel.setFrame(f,display:true,animate:false) }
        if state.captureBusy && activity == nil {
            activity = ProcessInfo.processInfo.beginActivity(options:[.userInitiatedAllowingIdleSystemSleep,.latencyCritical],reason:"保持用户启动的课堂采集和悬浮提示")
        } else if !state.captureBusy,let activity {ProcessInfo.processInfo.endActivity(activity);self.activity=nil}
    }
    @objc func toggleHints() { hintsVisible.toggle();UserDefaults.standard.set(hintsVisible,forKey:"showHints");render() }
    @objc func showPanel() {panel.orderFrontRegardless()}
    @objc func hidePanel() {panel.orderOut(nil)}
    @objc func showMain() {if mainWindow.isMiniaturized {mainWindow.deminiaturize(nil)};mainWindow.makeKeyAndOrderFront(nil);NSApp.activate(ignoringOtherApps:true)}
    @objc func minimizeMain() {mainWindow.miniaturize(nil);showPanel()}
    func windowDidMiniaturize(_ notification: Notification) {showPanel()}
    func windowShouldClose(_ sender: NSWindow) -> Bool {if sender === mainWindow {mainWindow.orderOut(nil);showPanel();return false};return true}
    func applicationShouldHandleReopen(_ sender:NSApplication,hasVisibleWindows flag:Bool)->Bool {showMain();showPanel();return true}
    func applicationShouldTerminateAfterLastWindowClosed(_ sender:NSApplication)->Bool {false}
    @objc func quitApp() {NSApp.terminate(nil)}
    func applicationShouldTerminate(_ sender:NSApplication)->NSApplication.TerminateReply {
        if closing || !state.captureBusy {return .terminateNow}
        closing=true
        webView.evaluateJavaScript("window.TMCRADesktop?.command('stop')")
        DispatchQueue.main.asyncAfter(deadline:.now()+1) { [weak self] in
            self?.webView.setMicrophoneCaptureState(.none) {NSApp.reply(toApplicationShouldTerminate:true)}
        }
        return .terminateLater
    }
    func applicationWillTerminate(_ notification:Notification) {
        nativeTimer?.invalidate();if let activity {ProcessInfo.processInfo.endActivity(activity)}
        webView.configuration.userContentController.removeScriptMessageHandler(forName:"desktop")
    }
    @objc func toggleCapture() {
        guard !commandPending else {return}
        let action=state.captureBusy ? "stop" : "start"
        guard state.captureBusy ? state.canStop : state.canStart else {return}
        commandPending=true;render()
        webView.evaluateJavaScript("window.TMCRADesktop?.command('\(action)')") { [weak self] _,error in
            guard let self else {return}
            self.commandPending=false
            if error != nil { self.state.status="控制未确认，请返回主窗口检查";if action=="stop" {self.webView.setMicrophoneCaptureState(.none){}} }
            self.pollPage();self.render()
        }
    }
    func pollPage() {
        guard !closing else {return}
        pollCount += 1
        if !isBundledPage && pollCount % 5 == 0 { checkHealth() }
        webView.evaluateJavaScript("window.TMCRADesktop?.publish(true)") { [weak self] _,error in
            guard let self else {return}
            if error != nil && !self.isBundledPage {self.state.connected=false;self.state.canStart=false;self.state.status="工作台暂不可用";self.render()}
        }
    }
    func checkHealth() {
        guard !healthInFlight,!isBundledPage else {return}
        healthInFlight=true
        let origin=configuredURL
        URLSession.shared.dataTask(with:URLRequest(url:origin.appendingPathComponent("healthz"),timeoutInterval:4)) { [weak self] data,response,error in
            let json=data.flatMap {try? JSONSerialization.jsonObject(with:$0) as? [String:Any]}
            let healthy=error == nil && (response as? HTTPURLResponse)?.statusCode == 200 && json?["status"] as? String == "ok"
            DispatchQueue.main.async {
                guard let self else {return};self.healthInFlight=false
                guard !self.isBundledPage,self.configuredURL==origin else {return}
                self.serverHealthy=healthy
                if !healthy {self.state.connected=false;self.state.canStart=false;self.state.status="服务连接已断开，可停止采集或重新连接";self.render()}
            }
        }.resume()
    }
    func userContentController(_ userContentController:WKUserContentController,didReceive message:WKScriptMessage) {
        guard message.frameInfo.isMainFrame, let body=message.body as? [String:Any] else {return}
        let source=message.frameInfo.request.url
        let trusted = isBundledPage ? WorkbenchAddress.bundledPage(source,resources:Bundle.main.resourceURL) : WorkbenchAddress.sameOrigin(source,configuredURL)
        guard trusted else {return}
        if body["type"] as? String == "action",isBundledPage {
            if body["action"] as? String == "demo" {openDemo()}
            if body["action"] as? String == "connect" {connectDialog()}
        } else if body["type"] as? String == "state" {
            state=PanelState(body);state.demo=isDemo
            if !isBundledPage && serverHealthy == false {state.connected=false;state.canStart=false;state.status="服务连接已断开，可停止采集或重新连接"}
            lastBridgeUpdate=Date();render()
        }
    }
    func webView(_ webView:WKWebView,didFinish navigation:WKNavigation!) {if !isBundledPage {checkHealth()};pollPage()}
    func webView(_ webView:WKWebView,didFailProvisionalNavigation navigation:WKNavigation!,withError error:Error) {
        guard !isBundledPage else {return};loadBundled("offline");state.status="服务器未连接，稍后再连接即可";render()
    }
    func webView(_ webView:WKWebView,decidePolicyFor navigationAction:WKNavigationAction,decisionHandler:@escaping(WKNavigationActionPolicy)->Void) {
        guard let url=navigationAction.request.url else {decisionHandler(.cancel);return}
        let localFile = isBundledPage && WorkbenchAddress.bundledPage(url,resources:Bundle.main.resourceURL)
        let allowed = localFile || (!isBundledPage && WorkbenchAddress.sameOrigin(url,configuredURL))
        decisionHandler(allowed ? .allow : .cancel)
    }
    func webView(_ webView:WKWebView,requestMediaCapturePermissionFor origin:WKSecurityOrigin,initiatedByFrame frame:WKFrameInfo,type:WKMediaCaptureType,decisionHandler:@escaping(WKPermissionDecision)->Void) {
        guard !isBundledPage,!isDemo,type == .microphone,frame.isMainFrame,WorkbenchAddress.sameOrigin(frame.request.url,configuredURL) else {decisionHandler(.deny);return}
        // Keep the OS/WebKit permission prompt: a floating-window click is not an automatic grant.
        if AVCaptureDevice.authorizationStatus(for:.audio) != .authorized {showMain()}
        decisionHandler(.prompt)
    }
    func webView(_ webView:WKWebView,runOpenPanelWith parameters:WKOpenPanelParameters,initiatedByFrame frame:WKFrameInfo,completionHandler:@escaping([URL]?)->Void) {
        let picker=NSOpenPanel();picker.canChooseDirectories=false;picker.allowsMultipleSelection=false
        picker.beginSheetModal(for:mainWindow){result in completionHandler(result == .OK ? picker.urls : nil)}
    }
    func webView(_ webView:WKWebView,runJavaScriptAlertPanelWithMessage message:String,initiatedByFrame frame:WKFrameInfo,completionHandler:@escaping()->Void) {alert(String(message.prefix(500)));completionHandler()}
    func alert(_ message:String) {let a=NSAlert();a.messageText="TMCRA 随堂助手";a.informativeText=message;a.addButton(withTitle:"好");a.runModal()}
}

let app=NSApplication.shared
let controller=DesktopApp()
app.delegate=controller
app.run()
