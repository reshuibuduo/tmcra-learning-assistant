import Foundation
@main struct ModelTests {
    static func main() {
        var count = 0
        func check(_ condition: @autoclosure () -> Bool,_ name:String) {
            guard condition() else {fputs("FAIL: \(name)\n",stderr);exit(1)};count += 1
        }
        check(!PanelState().canStart,"offline cannot start")
        check(!PanelState(["canStart":true]).canStart,"page flag cannot bypass offline")
        check(PanelState(["connected":true,"canStart":true]).canStart,"ready state starts")
        check(!PanelState(["connected":true,"captureBusy":true,"canStart":true]).canStart,"busy cannot start twice")
        check(PanelState(["captureBusy":true,"canStop":true]).canStop,"offline still permits stop")
        check(!PanelState(["captureBusy":false,"canStop":true]).canStop,"idle cannot stop")
        check(PanelState(["title":String(repeating:"a",count:500)]).title.count==120,"title bounded")
        check(PanelState(["card":["title":"A","explanation":String(repeating:"b",count:900)]]).card.explanation.count==450,"card bounded")
        check(PanelState(["card":["title":"A"]]).card.isEmpty,"incomplete card hidden")
        check(WorkbenchAddress.validate("http://127.0.0.1:18782/#login=once") != nil,"one-time login link accepted")
        check(WorkbenchAddress.validate("https://localhost:8443/") != nil,"localhost https accepted")
        check(WorkbenchAddress.validate("http://127.0.0.1.evil.invalid/")==nil,"lookalike host rejected")
        check(WorkbenchAddress.validate("https://example.com/")==nil,"unconfigured remote rejected")
        check(WorkbenchAddress.validate("file:///etc/passwd")==nil,"file URL rejected")
        check(WorkbenchAddress.validate("http://user:secret@127.0.0.1/")==nil,"userinfo rejected")
        check(WorkbenchAddress.validate("http://127.0.0.1/?token=secret")==nil,"query credential rejected")
        check(!WorkbenchAddress.sameOrigin(URL(string:"http://127.0.0.1:1"),URL(string:"http://127.0.0.1:2")),"port origin bound")
        check(WorkbenchAddress.withoutSecret(URL(string:"http://127.0.0.1:18782/#login=secret")!).fragment==nil,"login fragment not persisted")
        check(WorkbenchAddress.bundledPage(URL(fileURLWithPath:"/test/Resources/demo.html"),resources:URL(fileURLWithPath:"/test/Resources")),"resource slash normalization")
        check(!WorkbenchAddress.bundledPage(URL(fileURLWithPath:"/other/demo.html"),resources:URL(fileURLWithPath:"/test/Resources")),"untrusted local HTML rejected")
        print("MODEL_TESTS=\(count) PASS")
    }
}
