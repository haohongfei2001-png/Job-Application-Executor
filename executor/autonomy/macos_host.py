"""Compiled Cocoa/WebKit presenter for the opt-in owned app bootstrap.

The host accepts in-memory ConsumerSurface routes only. This provides one window
per presenter; cross-launch reuse/default native promotion remain separate gates.
No external-browser fallback or task writer.
"""
from __future__ import annotations

import hashlib
import json
import os
import select
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from .consumer_presentation import ConsumerSurface

HOST_RECEIPT = "native-host-manifest.json"
HOST_SOURCE = r"""
#import <Cocoa/Cocoa.h>
#import <WebKit/WebKit.h>

static void report(NSDictionary *object) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:object options:0 error:nil];
    if (data) {
        fwrite(data.bytes, 1, data.length, stdout);
        fputc('\n', stdout);
        fflush(stdout);
    }
}
static BOOL loopback(NSURL *url) {
    return [url.scheme isEqualToString:@"http"]
        && [url.host isEqualToString:@"127.0.0.1"]
        && url.port != nil && url.port.integerValue > 0
        && url.port.integerValue < 65536
        && url.user == nil && url.password == nil && url.fragment == nil
        && [url.absoluteString hasPrefix:[NSString stringWithFormat:@"http://127.0.0.1:%ld/", (long)url.port.integerValue]];
}
static NSString *origin(NSURL *url) {
    return [NSString stringWithFormat:@"http://127.0.0.1:%ld", (long)url.port.integerValue];
}
static BOOL initial(NSDictionary *command) {
    if (![command isKindOfClass:[NSDictionary class]]
        || command.count != 5 || ![command[@"command"] isEqual:@"present"]
        || ![command[@"request"] isKindOfClass:[NSNumber class]]
        || CFGetTypeID((__bridge CFTypeRef)command[@"request"]) == CFBooleanGetTypeID()
        || ![command[@"surface"] isKindOfClass:[NSString class]]
        || ![command[@"url"] isKindOfClass:[NSString class]]
        || ![command[@"service_origin"] isKindOfClass:[NSString class]]) return NO;
    NSString *text = command[@"url"], *service = command[@"service_origin"];
    if (text.length > 2048 || [text rangeOfCharacterFromSet:NSCharacterSet.controlCharacterSet].location != NSNotFound) return NO;
    NSURL *url = [NSURL URLWithString:text], *serviceURL = [NSURL URLWithString:[service stringByAppendingString:@"/"]];
    if (!loopback(url) || !loopback(serviceURL) || ![origin(serviceURL) isEqual:service]) return NO;
    BOOL dashboard = [command[@"surface"] isEqual:@"dashboard"];
    BOOL bootstrap = [command[@"surface"] isEqual:@"bootstrap"];
    if (!dashboard && !bootstrap) return NO;
    if (![url.path isEqual:dashboard ? @"/ui-login" : @"/"]) return NO;
    NSArray<NSURLQueryItem *> *query = [NSURLComponents componentsWithURL:url resolvingAgainstBaseURL:NO].queryItems;
    if (query.count != 1 || ![query.firstObject.name isEqual:dashboard ? @"ticket" : @"token"]
        || !query.firstObject.value.length || query.firstObject.value.length > 512
        || [query.firstObject.value rangeOfCharacterFromSet:NSCharacterSet.controlCharacterSet].location != NSNotFound) return NO;
    return !dashboard || [origin(url) isEqual:service];
}
@interface JAEHost : NSObject <NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate>
@property NSWindow *window;
@property WKWebView *view;
@property NSString *surface;
@property NSString *initialOrigin;
@property NSString *serviceOrigin;
@property BOOL smoke;
@property BOOL consumerSmoke;
@property BOOL failureSmoke;
@property NSTextField *failureMessage;
@property WKNavigation *activeNavigation;
@property BOOL finished;
- (BOOL)allows:(NSURL *)url;
- (void)present:(NSDictionary *)command;
@end

@implementation JAEHost
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    WKWebViewConfiguration *configuration = [WKWebViewConfiguration new];
    configuration.websiteDataStore = WKWebsiteDataStore.defaultDataStore;
    self.view = [[WKWebView alloc] initWithFrame:NSMakeRect(0, 0, 1024, 760) configuration:configuration];
    self.view.navigationDelegate = self;
    self.view.pageZoom = 1.0;
    self.view.accessibilityLabel = @"AI 投递经理工作台";
    self.window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 1024, 760)
        styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable
        backing:NSBackingStoreBuffered defer:NO];
    self.window.title = @"AI 投递经理";
    self.window.minSize = NSMakeSize(640, 480);
    self.window.releasedWhenClosed = NO;
    self.window.delegate = self;
    NSView *content = [[NSView alloc] initWithFrame:self.view.frame];
    self.view.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
    [content addSubview:self.view];
    self.failureMessage = [NSTextField labelWithString:@"本机工作台暂不可用。请关闭窗口后重新打开；工作台没有自动重试，任务状态需重新连接后确认。"];
    self.failureMessage.frame = NSMakeRect(24, 24, 976, 92);
    self.failureMessage.autoresizingMask = NSViewWidthSizable | NSViewMaxYMargin;
    self.failureMessage.font = [NSFont systemFontOfSize:16];
    self.failureMessage.maximumNumberOfLines = 0;
    self.failureMessage.lineBreakMode = NSLineBreakByWordWrapping;
    self.failureMessage.accessibilityLabel = self.failureMessage.stringValue;
    self.failureMessage.hidden = YES;
    [content addSubview:self.failureMessage];
    self.window.contentView = content;
    [self.window center];
    NSMenu *menu = [NSMenu new], *edit = [NSMenu new], *viewMenu = [NSMenu new];
    NSMenuItem *editItem = [[NSMenuItem alloc] initWithTitle:@"编辑" action:nil keyEquivalent:@""];
    editItem.submenu = edit;
    for (NSArray *item in @[@[@"撤销", @"undo:", @"z"], @[@"重做", @"redo:", @"Z"], @[@"剪切", @"cut:", @"x"], @[@"复制", @"copy:", @"c"], @[@"粘贴", @"paste:", @"v"], @[@"全选", @"selectAll:", @"a"]]) {
        [edit addItemWithTitle:item[0] action:NSSelectorFromString(item[1]) keyEquivalent:item[2]];
    }
    NSMenuItem *viewItem = [[NSMenuItem alloc] initWithTitle:@"显示" action:nil keyEquivalent:@""];
    viewItem.submenu = viewMenu;
    NSMenuItem *larger = [viewMenu addItemWithTitle:@"放大" action:@selector(zoomIn:) keyEquivalent:@"+"];
    NSMenuItem *smaller = [viewMenu addItemWithTitle:@"缩小" action:@selector(zoomOut:) keyEquivalent:@"-"];
    NSMenuItem *normal = [viewMenu addItemWithTitle:@"实际大小" action:@selector(zoomReset:) keyEquivalent:@"0"];
    larger.target = smaller.target = normal.target = self;
    [menu addItem:editItem]; [menu addItem:viewItem]; NSApp.mainMenu = menu;
    [self.window makeKeyAndOrderFront:nil];
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        char *line = NULL; size_t capacity = 0;
        while (getline(&line, &capacity, stdin) > 0) {
            if (strlen(line) > 8192) { report(@{@"ok": @NO, @"reason": @"host_input_invalid"}); break; }
            NSData *data = [[NSString stringWithUTF8String:line] dataUsingEncoding:NSUTF8StringEncoding];
            NSDictionary *command = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
            dispatch_async(dispatch_get_main_queue(), ^{ [self present:command]; });
        }
        free(line);
    });
}
- (void)zoomIn:(id)sender { self.view.pageZoom = MIN(2.0, self.view.pageZoom + 0.1); }
- (void)zoomOut:(id)sender { self.view.pageZoom = MAX(0.5, self.view.pageZoom - 0.1); }
- (void)zoomReset:(id)sender { self.view.pageZoom = 1.0; }
- (BOOL)allows:(NSURL *)url {
    if (!loopback(url)) return NO;
    NSString *current = origin(url);
    if ([current isEqual:self.serviceOrigin] && ([url.path isEqual:@"/ui"] || [url.path isEqual:@"/ui-login"])) return YES;
    return [self.surface isEqual:@"bootstrap"] && [current isEqual:self.initialOrigin]
        && ([url.path isEqual:@"/"] || [url.path isEqual:@"/retry"]);
}
- (void)present:(NSDictionary *)command {
    if (!initial(command)) { report(@{@"ok": @NO, @"reason": @"host_input_invalid"}); return; }
    NSURL *url = [NSURL URLWithString:command[@"url"]];
    self.surface = command[@"surface"]; self.initialOrigin = origin(url); self.serviceOrigin = command[@"service_origin"];
    self.failureMessage.hidden = YES; self.view.hidden = NO;
    self.activeNavigation = [self.view loadRequest:[NSURLRequest requestWithURL:url cachePolicy:NSURLRequestReloadIgnoringLocalCacheData timeoutInterval:10]];
    [self.window makeKeyAndOrderFront:nil];
    report(@{@"ok": @YES, @"request": command[@"request"], @"surface": self.surface, @"window_count": @1});
}
- (void)webView:(WKWebView *)webView decidePolicyForNavigationAction:(WKNavigationAction *)action decisionHandler:(void (^)(WKNavigationActionPolicy))handler {
    handler([self allows:action.request.URL] ? WKNavigationActionPolicyAllow : WKNavigationActionPolicyCancel);
}
- (void)webView:(WKWebView *)webView decidePolicyForNavigationResponse:(WKNavigationResponse *)response decisionHandler:(void (^)(WKNavigationResponsePolicy))handler {
    BOOL permitted = [self allows:response.response.URL];
    BOOL unhealthy = permitted && response.isForMainFrame && [response.response isKindOfClass:[NSHTTPURLResponse class]]
        && ((NSHTTPURLResponse *)response.response).statusCode >= 400;
    handler(permitted && !unhealthy ? WKNavigationResponsePolicyAllow : WKNavigationResponsePolicyCancel);
    if (unhealthy) [self failed];
}
- (void)webView:(WKWebView *)webView didFailNavigation:(WKNavigation *)navigation withError:(NSError *)error {
    if (navigation == self.activeNavigation && !([error.domain isEqual:NSURLErrorDomain] && error.code == NSURLErrorCancelled)) [self failed];
}
- (void)webView:(WKWebView *)webView didFailProvisionalNavigation:(WKNavigation *)navigation withError:(NSError *)error {
    if (navigation == self.activeNavigation && !([error.domain isEqual:NSURLErrorDomain] && error.code == NSURLErrorCancelled)) [self failed];
}
- (void)failed {
    // Static native recovery UI: never render an NSError, URL or capability.
    self.view.hidden = YES; self.failureMessage.hidden = NO;
    if (self.failureSmoke && !self.finished) {
        self.finished = YES;
        BOOL visible = !self.failureMessage.hidden && self.view.hidden
            && [self.failureMessage.accessibilityLabel isEqual:self.failureMessage.stringValue];
        report(@{@"ok": @(visible), @"native_error_visible": @(visible),
                 @"window_count": @1, @"no_automatic_retry": @YES});
        [NSApp terminate:nil];
    } else if (self.smoke && !self.finished) {
        self.finished = YES; report(@{@"ok": @NO, @"reason": @"native_page_failed"}); [NSApp terminate:nil];
    }
}
- (void)webView:(WKWebView *)webView didFinishNavigation:(WKNavigation *)navigation {
    if (navigation != self.activeNavigation) return;
    self.failureMessage.hidden = YES; self.view.hidden = NO;
    if (self.failureSmoke && !self.finished) {
        self.finished = YES; report(@{@"ok": @NO, @"reason": @"expected_native_failure_missing"}); [NSApp terminate:nil]; return;
    }
    if (!self.smoke || self.finished) return;
    // Fixed cloud-only smoke probe, no arbitrary script input or task action.
    NSString *probe = self.consumerSmoke
        ? @"!!(document.getElementById('readiness') && document.getElementById('message') && document.querySelector('input[name=company]'))"
        : @"document.getElementById('native-canary')?.textContent === 'SYNTHETIC_NATIVE_CANARY'";
    [webView evaluateJavaScript:probe
        completionHandler:^(id result, NSError *error) {
        if (navigation != self.activeNavigation || self.finished) return;
        self.finished = YES;
        [self zoomIn:nil]; BOOL zoom = self.view.pageZoom > 1.0; [self zoomReset:nil];
        BOOL denied = ![self allows:[NSURL URLWithString:@"https://example.invalid/"]]
            && ![self allows:[NSURL URLWithString:@"file:///tmp/forbidden"]]
            && ![self allows:[NSURL URLWithString:@"http://127.0.0.1:1/ui"]];
        BOOL ok = !error && [result isEqual:@YES] && zoom && denied;
        report(@{@"ok": @(ok), @"native_page": @(ok), @"window_count": @1,
                 @"zoom_reset": @(self.view.pageZoom == 1.0), @"external_navigation_denied": @(denied)});
        [NSApp terminate:nil];
    }];
}
- (void)windowWillClose:(NSNotification *)notification { [NSApp terminate:nil]; }
@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        JAEHost *host = [JAEHost new];
        host.consumerSmoke = argc == 2 && strcmp(argv[1], "--consumer-smoke") == 0;
        host.failureSmoke = argc == 2 && strcmp(argv[1], "--failure-smoke") == 0;
        host.smoke = host.consumerSmoke || host.failureSmoke || (argc == 2 && strcmp(argv[1], "--smoke") == 0);
        [NSApplication sharedApplication];
        NSApp.delegate = host;
        [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
        if (host.smoke) {
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 15 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{
                if (!host.finished) { report(@{@"ok": @NO, @"reason": @"native_page_deadline"}); [NSApp terminate:nil]; }
            });
        }
        [NSApp run];
    }
    return 0;
}
"""


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _no_alias(path: Path) -> None:
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("native_host_path_invalid")


def verify_native_host(directory: str | Path, *, expected_source_sha256: str | None = None) -> bool:
    expected = (hashlib.sha256(HOST_SOURCE.encode()).hexdigest()
                if expected_source_sha256 is None else expected_source_sha256)
    if not isinstance(expected, str) or len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        return False
    root = Path(directory).absolute()
    executable, receipt = root / "AIApplicationWindow", root / HOST_RECEIPT
    try:
        _no_alias(root)
        if executable.is_symlink() or receipt.is_symlink() or not executable.is_file() or not receipt.is_file():
            return False
        saved = json.loads(receipt.read_text(encoding="utf-8"))
        return (saved == {"format": "jae-native-host-v1",
                         "source_sha256": expected,
                         "executable_sha256": _digest(executable)}
                and bool(executable.stat().st_mode & stat.S_IXUSR)
                and {path.name for path in root.iterdir()} == {"AIApplicationWindow", HOST_RECEIPT})
    except (OSError, UnicodeError, ValueError, TypeError):
        return False


def build_native_host(destination: str | Path) -> dict:
    if sys.platform != "darwin":
        raise ValueError("native_host_macos_required")
    target = Path(destination).absolute()
    _no_alias(target)
    if target.exists() or not target.parent.is_dir():
        raise ValueError("native_host_output_unavailable")
    target.mkdir(mode=0o700)
    try:
        with tempfile.TemporaryDirectory(prefix="jae-native-host-") as temporary:
            source = Path(temporary) / "window.m"
            source.write_text(HOST_SOURCE, encoding="utf-8")
            # Fixed system compiler/frameworks. Private routes never enter argv.
            result = subprocess.run(
                ["/usr/bin/xcrun", "clang", "-fobjc-arc", "-mmacosx-version-min=13.0",
                 "-framework", "Cocoa", "-framework", "WebKit",
                 str(source), "-o", str(target / "AIApplicationWindow")],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=60, check=False)
            if result.returncode != 0:
                raise ValueError("native_host_compile_failed")
        executable = target / "AIApplicationWindow"
        executable.chmod(0o755)
        receipt = {"format": "jae-native-host-v1",
                   "source_sha256": hashlib.sha256(HOST_SOURCE.encode()).hexdigest(),
                   "executable_sha256": _digest(executable)}
        with (target / HOST_RECEIPT).open("x", encoding="utf-8") as file:
            json.dump(receipt, file, sort_keys=True)
            file.flush()
            os.fsync(file.fileno())
        if not verify_native_host(target):
            raise ValueError("native_host_candidate_invalid")
        return receipt
    except BaseException:
        # Only remove artifacts authored in this fresh private candidate.
        for name in ("AIApplicationWindow", HOST_RECEIPT):
            (target / name).unlink(missing_ok=True)
        try:
            target.rmdir()
        except OSError:
            pass
        raise


def native_command(surface: ConsumerSurface, request: int) -> dict:
    try:
        if (not isinstance(surface, ConsumerSurface) or type(request) is not int
                or request < 1):
            raise ValueError
        # Revalidate manually constructed dataclasses before passing to Cocoa.
        checked = ConsumerSurface.from_url(
            surface.surface, surface.url,
            service_port=int(surface._service_origin.rsplit(":", 1)[1]))
        if checked != surface:
            raise ValueError
        return {"command": "present", "request": request, "surface": checked.surface,
                "url": checked.url, "service_origin": checked._service_origin}
    except (ValueError, TypeError, AttributeError, IndexError):
        raise ValueError("invalid consumer presentation") from None


class NativePresenter:
    """One trusted native window, no fallback and no credential report."""
    def __init__(self, directory: str | Path, *, consumer_smoke: bool = False):
        if type(consumer_smoke) is not bool:
            raise ValueError("native_host_mode_invalid")
        self.consumer_smoke = consumer_smoke
        self.directory = Path(directory).absolute()
        self.process = None
        self.sequence = 0

    def __repr__(self) -> str:
        return "<NativePresenter credential_in_report=False>"

    def __call__(self, surface: ConsumerSurface) -> bool:
        try:
            self.sequence += 1
            command = native_command(surface, self.sequence)
            if sys.platform != "darwin" or not verify_native_host(self.directory):
                return False
            if self.process is None or self.process.poll() is not None:
                self.process = subprocess.Popen(
                    [str(self.directory / "AIApplicationWindow"),
                     *(["--consumer-smoke"] if self.consumer_smoke else [])],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, text=True, bufsize=1)
            self.process.stdin.write(json.dumps(command, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
            ready, _, _ = select.select([self.process.stdout], [], [], 5)
            if not ready:
                self.close()
                return False
            reply = json.loads(self.process.stdout.readline(4096))
            accepted = (isinstance(reply, dict)
                        and type(reply.get("request")) is int
                        and type(reply.get("window_count")) is int
                        and reply == {"ok": True, "request": self.sequence,
                                 "surface": surface.surface, "window_count": 1})
            if not accepted:
                self.close()
            return accepted
        except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
            self.close()
            return False


    def wait_for_close(self) -> bool:
        """Parent lifetime follows its actual NSWindow, without a second writer."""
        if self.process is None:
            return False
        process = self.process
        try:
            closed = process.wait() == 0
            if self.consumer_smoke:
                reply = json.loads(process.stdout.readline(4096))
                return (closed and reply == {"ok": True, "native_page": True,
                    "window_count": 1, "zoom_reset": True,
                    "external_navigation_denied": True})
            return closed
        except (OSError, ValueError, TypeError, subprocess.SubprocessError):
            return False
        finally:
            self.close()

    def close(self) -> None:
        process, self.process = self.process, None
        if process is not None:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()
