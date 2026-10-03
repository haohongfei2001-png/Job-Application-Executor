"""Compiled Cocoa/WebKit presenter for the opt-in owned app bootstrap.

The host accepts in-memory ConsumerSurface routes and finite local release outcomes.
Owned presentation and cross-launch reuse remain separate admission gates.
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
import threading
import time
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
static BOOL focusRequest(NSDictionary *command) {
    if (![command isKindOfClass:[NSDictionary class]] || command.count != 2
        || ![command[@"command"] isEqual:@"focus"]
        || ![command[@"request"] isKindOfClass:[NSNumber class]]
        || CFGetTypeID((__bridge CFTypeRef)command[@"request"]) == CFBooleanGetTypeID()
        || CFNumberIsFloatType((__bridge CFNumberRef)command[@"request"])
        || [command[@"request"] longLongValue] < 1) return NO;
    return YES;
}
static BOOL releaseResult(NSDictionary *command) {
    return [command isKindOfClass:[NSDictionary class]] && command.count == 2
        && [command[@"command"] isEqual:@"release-result"]
        && [command[@"result"] isKindOfClass:[NSString class]]
        && ([command[@"result"] isEqual:@"not-confirmed"]
            || [command[@"result"] isEqual:@"reopen-failed"]);
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
@property BOOL focusSmoke;
@property BOOL releaseSmoke;
@property BOOL releaseResultMode;
@property NSString *installerMode;
@property BOOL installerSmoke;
@property BOOL firstUseRecoveryMode;
@property BOOL firstUseRecoverySmoke;
@property BOOL firstInstallMode;
@property BOOL firstInstallSmoke;
@property NSString *firstInstallResult;
@property BOOL releaseResultSmoke;
@property (strong) NSDictionary *releaseRequest;
@property NSUInteger focusCount;
@property (strong) NSDictionary *focusSnapshot;
@property (strong) WKNavigation *focusNavigation;
@property (strong) WKWebView *focusView;
@property NSTextField *failureMessage;
@property WKNavigation *activeNavigation;
@property BOOL finished;
@property BOOL windowClosing;
- (BOOL)allows:(NSURL *)url;
- (BOOL)focusWindow;
- (void)observeFocus;
- (void)present:(NSDictionary *)command;
- (void)commitRelease:(NSDictionary *)request;
@end

@implementation JAEHost
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    if (self.installerMode) {
        NSAlert *alert = [NSAlert new];
        alert.messageText = @"AI 投递经理已安装";
        BOOL updating = [self.installerMode isEqualToString:@"update"];
        BOOL pending = [self.installerMode isEqualToString:@"recovery"];
        alert.informativeText = updating
            ? @"可更新到本安装包版本。请先保存输入并关闭已打开的应用窗口；现有任务与已保存答案会保留。版本、任务或服务状态无法核对时会停止，不会强制关闭窗口、迁移旧资料或自动重试。"
            : pending ? @"上次首次使用尚未完成。请打开已安装应用进行核对；本安装包不会替换它或删除任何资料。"
                      : @"本机已有相同版本。直接打开即可，不会重复安装或另建任务库。";
        [alert addButtonWithTitle:updating ? @"更新并打开" : pending ? @"打开并核对首次使用" : @"打开已安装应用"];
        if (updating) [alert addButtonWithTitle:@"打开已安装应用"];
        [alert addButtonWithTitle:@"取消"];
        [NSApp activateIgnoringOtherApps:YES];
        if (self.installerSmoke) {
            [alert.window makeKeyAndOrderFront:nil];
            BOOL visible = alert.window.isVisible && self.view == nil;
            [alert.window close]; self.finished = YES;
            report(@{@"ok": @(visible), @"installer_prompt_visible": @(visible),
                     @"installer_choice": @{@"action": @"cancel"}});
            [NSApp terminate:nil]; return;
        }
        NSModalResponse response = [alert runModal];
        NSString *action = response == NSAlertFirstButtonReturn ? (updating ? @"update" : @"open")
            : updating && response == NSAlertSecondButtonReturn ? @"open" : @"cancel";
        self.finished = YES;
        report(@{@"ok": @YES, @"installer_choice": @{@"action": action}});
        [NSApp terminate:nil]; return;
    }
    if (self.firstUseRecoveryMode) {
        // Only explicit revalidation of an already admitted pending first use.
        // No path picker, WebView, credentials or service control in this host.
        NSAlert *alert = [NSAlert new];
        alert.messageText = @"继续核对首次使用";
        alert.informativeText = @"上次首次使用尚未完成。继续后仅核对当前应用和空任务状态；发现旧任务、文件变化或占用时会停止并保留原状，不会自动搬迁、重装或删除文件。";
        [alert addButtonWithTitle:@"重新核对并完成首次使用"];
        [alert addButtonWithTitle:@"取消"];
        [NSApp activateIgnoringOtherApps:YES];
        if (self.firstUseRecoverySmoke) {
            [alert.window makeKeyAndOrderFront:nil];
            BOOL visible = alert.window.isVisible && self.view == nil;
            [alert.window close]; self.finished = YES;
            report(@{@"ok": @(visible), @"first_use_recovery_prompt_visible": @(visible),
                     @"first_use_recovery_choice": @{@"action": @"cancel"}});
            [NSApp terminate:nil]; return;
        }
        NSModalResponse response = [alert runModal];
        self.finished = YES;
        report(@{@"ok": @YES, @"first_use_recovery_choice":
            @{@"action": response == NSAlertFirstButtonReturn ? @"resume_first_use" : @"cancel"}});
        [NSApp terminate:nil]; return;
    }
    if (self.firstInstallMode) {
        // Static local install/continuity choice. No WebView, URL or service.
        NSAlert *alert = [NSAlert new];
        alert.messageText = @"首次安装 AI 投递经理";
        alert.informativeText = @"应用将安装到当前用户的“应用程序”文件夹，不需要终端。已有应用不会覆盖，旧任务不会自动搬迁。若使用过旧版，请选择旧版项目文件夹核对；发现旧任务时会保留并停止安装。";
        if (self.firstInstallResult) {
            alert.messageText = @"安装尚未完成";
            NSDictionary *messages = @{
                @"legacy-state-required": @"发现旧版任务或无法核对旧版资料。原应用、任务和答案保持原处；需要完成安全迁移后再安装。请勿删除旧文件或改选空文件夹绕过检查。",
                @"app-in-use": @"请先保存输入并关闭已打开的 AI 投递经理窗口，再重新打开本安装包。没有强制关闭窗口、替换应用或删除任务。",
                @"target-occupied": @"当前用户的应用程序文件夹已有同名应用，安装包没有替换它。请关闭此窗口并打开已有应用；如需更新，使用已有应用内的更新入口。",
                @"state-continuity-required": @"发现没有对应应用的既有任务状态。为避免另建空任务库，安装已停止，原状态保持不变。",
                @"not-confirmed": @"安装结果尚未确认。没有自动重试或打开另一套任务库；请保留原应用和安装包，核对后再操作。",
                @"reopen-failed": @"应用已安装，但工作台尚未确认打开。请从当前用户的应用程序文件夹打开 AI 投递经理，核对任务状态。"};
            NSString *message = messages[self.firstInstallResult];
            if (!message) { report(@{@"ok": @NO}); [NSApp terminate:nil]; return; }
            alert.informativeText = message;
            [alert addButtonWithTitle:@"关闭"];
        } else {
            [alert addButtonWithTitle:@"选择旧版项目文件夹…"];
            [alert addButtonWithTitle:@"首次使用，没有旧版任务"];
            [alert addButtonWithTitle:@"取消"];
        }
        [NSApp activateIgnoringOtherApps:YES];
        if (self.firstInstallSmoke) {
            [alert.window makeKeyAndOrderFront:nil];
            BOOL visible = alert.window.isVisible && self.view == nil;
            [alert.window close]; self.finished = YES;
            report(@{@"ok": @(visible), @"first_install_prompt_visible": @(visible),
                     @"first_install_choice": @{@"action": @"cancel"}});
            [NSApp terminate:nil]; return;
        }
        NSModalResponse response = [alert runModal];
        NSDictionary *choice = @{@"action": @"cancel"};
        if (!self.firstInstallResult && response == NSAlertSecondButtonReturn) {
            choice = @{@"action": @"install", @"continuity": @"first_use"};
        } else if (!self.firstInstallResult && response == NSAlertFirstButtonReturn) {
            NSOpenPanel *panel = [NSOpenPanel openPanel];
            panel.title = @"选择旧版 Job-Application-Executor 项目文件夹";
            panel.canChooseFiles = NO; panel.canChooseDirectories = YES;
            panel.allowsMultipleSelection = NO; panel.canCreateDirectories = NO;
            if ([panel runModal] == NSModalResponseOK && panel.URLs.count == 1) {
                NSURL *selected = panel.URLs.firstObject;
                if (selected.isFileURL && selected.path.isAbsolutePath && selected.path.length <= 2048
                    && [selected.path rangeOfCharacterFromSet:NSCharacterSet.controlCharacterSet].location == NSNotFound) {
                    choice = @{@"action": @"install", @"continuity": @"selected_legacy_directory",
                               @"legacy_directory": selected.path};
                }
            }
        }
        self.finished = YES;
        report(@{@"ok": @YES, @"first_install_choice": choice});
        [NSApp terminate:nil]; return;
    }
    if (self.releaseResultMode) {
        // A finite local result has no web view, URL, task control or installer.
        self.window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 640, 240)
            styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable
            backing:NSBackingStoreBuffered defer:NO];
        self.window.title = @"AI 投递经理";
        self.window.minSize = NSMakeSize(480, 220);
        self.window.releasedWhenClosed = NO; self.window.delegate = self;
        self.failureMessage = [NSTextField labelWithString:@""];
        self.failureMessage.frame = NSMakeRect(24, 32, 592, 160);
        self.failureMessage.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
        self.failureMessage.font = [NSFont systemFontOfSize:16];
        self.failureMessage.maximumNumberOfLines = 0;
        self.failureMessage.lineBreakMode = NSLineBreakByWordWrapping;
        [self.window.contentView addSubview:self.failureMessage];
        [self.window center];
        dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
            char *line = NULL; size_t capacity = 0;
            ssize_t count = getline(&line, &capacity, stdin);
            if (count <= 0 || count > 1024) {
                free(line); report(@{@"ok": @NO, @"reason": @"host_result_invalid"});
                dispatch_async(dispatch_get_main_queue(), ^{ [NSApp terminate:nil]; }); return;
            }
            NSData *data = [[NSString stringWithUTF8String:line] dataUsingEncoding:NSUTF8StringEncoding];
            NSDictionary *command = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
            free(line);
            dispatch_async(dispatch_get_main_queue(), ^{ [self present:command]; });
        });
        return;
    }
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
    self.view.frame = NSMakeRect(0, 0, 1024, 716);
    [content addSubview:self.view];
    NSPopUpButton *appActions = [[NSPopUpButton alloc] initWithFrame:NSMakeRect(820, 722, 184, 30) pullsDown:YES];
    appActions.autoresizingMask = NSViewMinXMargin | NSViewMinYMargin;
    appActions.accessibilityLabel = @"应用更新和回退";
    [appActions addItemWithTitle:@"应用"];
    [appActions addItemWithTitle:@"从交付包更新…"];
    [appActions addItemWithTitle:@"回退到保留版本…"];
    appActions.itemArray[1].target = appActions.itemArray[2].target = self;
    appActions.itemArray[1].action = @selector(updateApplication:);
    appActions.itemArray[2].action = @selector(restoreApplication:);
    [content addSubview:appActions];
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
    NSMenu *releaseMenu = [NSMenu new];
    NSMenuItem *releaseItem = [[NSMenuItem alloc] initWithTitle:@"应用" action:nil keyEquivalent:@""];
    releaseItem.submenu = releaseMenu;
    NSMenuItem *update = [releaseMenu addItemWithTitle:@"从交付包更新…" action:@selector(updateApplication:) keyEquivalent:@""];
    NSMenuItem *restore = [releaseMenu addItemWithTitle:@"回退到保留版本…" action:@selector(restoreApplication:) keyEquivalent:@""];
    update.target = restore.target = self;
    [menu addItem:releaseItem]; [menu addItem:editItem]; [menu addItem:viewItem]; NSApp.mainMenu = menu;
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
- (BOOL)validateMenuItem:(NSMenuItem *)item {
    if (item.action == @selector(updateApplication:) || item.action == @selector(restoreApplication:))
        return self.surface != nil && !self.finished && !self.smoke;
    return YES;
}
- (BOOL)confirmRelease:(NSString *)title {
    if (!self.surface || self.finished || self.smoke) return NO;
    NSAlert *alert = [NSAlert new];
    alert.messageText = title;
    alert.informativeText = @"将关闭当前窗口并在安全停止点检查应用。请先保存窗口中尚未保存的输入。现有任务和已保存答案不会恢复成旧快照；忙碌、版本或状态无法核对时会拒绝操作。不会提交申请或下载文件。";
    [alert addButtonWithTitle:@"继续"]; [alert addButtonWithTitle:@"取消"];
    return [alert runModal] == NSAlertFirstButtonReturn;
}
- (void)commitRelease:(NSDictionary *)request {
    if (!self.surface || self.finished || self.releaseRequest) return;
    self.releaseRequest = request; self.finished = YES;
    [self.window close];
}
- (void)updateApplication:(id)sender {
    if (![self confirmRelease:@"从本地交付包更新应用？"]) return;
    NSOpenPanel *panel = [NSOpenPanel openPanel];
    panel.title = @"选择完整交付包文件夹";
    panel.canChooseFiles = NO; panel.canChooseDirectories = YES;
    panel.allowsMultipleSelection = NO; panel.canCreateDirectories = NO;
    if ([panel runModal] != NSModalResponseOK || panel.URLs.count != 1) return;
    NSURL *selected = panel.URLs.firstObject;
    if (!selected.isFileURL || !selected.path.isAbsolutePath || selected.path.length > 2048
        || [selected.path rangeOfCharacterFromSet:NSCharacterSet.controlCharacterSet].location != NSNotFound) return;
    [self commitRelease:@{@"action": @"update", @"distribution": selected.path}];
}
- (void)restoreApplication:(id)sender {
    if ([self confirmRelease:@"回退到保留的应用版本？"])
        [self commitRelease:@{@"action": @"restore"}];
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
- (BOOL)focusWindow {
    // An explicit owned reopen restores orientation only. No ticket, service
    // restart, navigation, task command or change to the WebKit editing state.
    if (!self.window || !self.surface || self.finished) return NO;
    if (self.window.isMiniaturized) [self.window deminiaturize:nil];
    [self.window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
    return YES;
}
- (BOOL)applicationShouldHandleReopen:(NSApplication *)sender hasVisibleWindows:(BOOL)visible {
    if (sender != NSApp) return NO;
    [self focusWindow];
    return NO; // The existing window handled it; never construct another.
}
- (void)observeFocus {
    // Fixed read-only cloud oracle. Applicant values stay in memory and are
    // never reported. Production focus does not execute a DOM probe.
    NSString *probe = @"(() => { const e=document.getElementById('native-draft'); return e ? {value:e.value,active:document.activeElement===e,start:e.selectionStart,end:e.selectionEnd} : null; })()";
    [self.view evaluateJavaScript:probe completionHandler:^(id result, NSError *error) {
        if (self.finished) return;
        BOOL snapshot = !error && [result isKindOfClass:[NSDictionary class]]
            && [result[@"active"] isEqual:@YES];
        if (!self.focusSnapshot) {
            if (!snapshot) {
                self.finished = YES; report(@{@"ok": @NO, @"reason": @"native_focus_fixture_unavailable"}); [NSApp terminate:nil]; return;
            }
            self.focusSnapshot = result;
            self.focusNavigation = self.activeNavigation;
            self.focusView = self.view;
            report(@{@"ok": @YES, @"focus_fixture_ready": @YES, @"window_count": @1});
            return;
        }
        BOOL retained = snapshot && [self.focusSnapshot isEqual:result]
            && self.focusNavigation == self.activeNavigation && self.focusView == self.view
            && !self.view.hidden && self.failureMessage.hidden
            && !self.window.isMiniaturized && self.window.isVisible;
        self.finished = YES;
        report(@{@"ok": @(retained), @"focus_only": @YES, @"window_count": @1,
                 @"draft_and_selection_retained": @(retained), @"reopen_count": @(self.focusCount)});
        [NSApp terminate:nil];
    }];
}
- (void)present:(NSDictionary *)command {
    if (self.releaseResultMode) {
        if (self.finished || !releaseResult(command)) {
            report(@{@"ok": @NO, @"reason": @"host_result_invalid"}); [NSApp terminate:nil]; return;
        }
        NSString *message = [command[@"result"] isEqual:@"reopen-failed"]
            ? @"应用版本已切换，但工作台未能自动打开。请关闭此窗口后重新打开应用，确认任务状态。任务没有自动重试，也不会自动提交申请。"
            : @"应用更新或回退暂未确认完成。请关闭此窗口后重新打开应用，检查应用和任务状态。任务没有自动重试，也不会自动提交申请。";
        self.failureMessage.stringValue = message;
        self.failureMessage.accessibilityLabel = message;
        [self.window makeKeyAndOrderFront:nil];
        BOOL visible = self.window.isVisible && !self.failureMessage.hidden
            && [self.failureMessage.accessibilityLabel isEqual:message] && self.view == nil
            && self.activeNavigation == nil && NSApp.mainMenu == nil;
        self.finished = YES;
        report(@{@"ok": @(visible), @"release_result_visible": @(visible),
                 @"window_count": @1, @"no_automatic_retry": @YES});
        if (self.releaseResultSmoke) [NSApp terminate:nil];
        return;
    }
    if (focusRequest(command)) {
        BOOL focused = [self focusWindow];
        if (!focused) { report(@{@"ok": @NO, @"reason": @"host_focus_unavailable"}); return; }
        report(@{@"ok": @YES, @"request": command[@"request"], @"surface": self.surface,
                 @"window_count": @1, @"focus_only": @YES});
        if (self.focusSmoke) {
            self.focusCount++;
            if (self.focusCount == 3) {
                // Exercise the ordinary Cocoa reopen delegate through the same
                // no-navigation path; neither event is a new presentation.
                [self applicationShouldHandleReopen:NSApp hasVisibleWindows:YES];
                [self applicationShouldHandleReopen:NSApp hasVisibleWindows:NO];
                [self observeFocus];
            }
        }
        return;
    }
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
    if (self.focusSmoke && !self.finished) { [self observeFocus]; return; }
    if (self.releaseSmoke && !self.finished) {
        // Fixed cloud oracle invokes the same terminal handoff; no installer,
        // path picker, task command or candidate execution inside this host.
        NSMenuItem *item = [NSApp.mainMenu itemWithTitle:@"应用"];
        if (!item || item.submenu.numberOfItems != 2) {
            self.finished = YES; report(@{@"ok": @NO, @"reason": @"release_menu_missing"}); [NSApp terminate:nil]; return;
        }
        [self commitRelease:@{@"action": @"restore"}]; return;
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
- (void)windowWillClose:(NSNotification *)notification {
    // AppKit termination can re-enter the close delegate. Consume the one
    // terminal intent before reporting or requesting application termination.
    if (self.windowClosing) return;
    self.windowClosing = YES; self.finished = YES;
    NSDictionary *request = self.releaseRequest;
    self.releaseRequest = nil;
    if (request) report(@{@"ok": @YES, @"release_request": request});
    [NSApp terminate:nil];
}
@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        JAEHost *host = [JAEHost new];
        host.firstUseRecoverySmoke = argc == 2 && strcmp(argv[1], "--first-use-recovery-smoke") == 0;
        for (NSString *mode in @[@"open", @"update", @"recovery"]) {
            NSString *flag = [@"--installer-" stringByAppendingString:mode];
            NSString *smokeFlag = [flag stringByAppendingString:@"-smoke"];
            if (argc == 2 && (strcmp(argv[1], flag.UTF8String) == 0 || strcmp(argv[1], smokeFlag.UTF8String) == 0)) {
                host.installerMode = mode;
                host.installerSmoke = strcmp(argv[1], smokeFlag.UTF8String) == 0;
            }
        }
        host.firstUseRecoveryMode = host.firstUseRecoverySmoke || (argc == 2 && strcmp(argv[1], "--first-use-recovery") == 0);
        host.firstInstallSmoke = argc == 2 && strcmp(argv[1], "--first-install-smoke") == 0;
        host.firstInstallMode = host.firstInstallSmoke || (argc == 2 && strcmp(argv[1], "--first-install") == 0)
            || (argc == 3 && strcmp(argv[1], "--first-install-result") == 0);
        if (argc == 3 && strcmp(argv[1], "--first-install-result") == 0)
            host.firstInstallResult = [NSString stringWithUTF8String:argv[2]];
        host.consumerSmoke = argc == 2 && strcmp(argv[1], "--consumer-smoke") == 0;
        host.failureSmoke = argc == 2 && strcmp(argv[1], "--failure-smoke") == 0;
        host.focusSmoke = argc == 2 && strcmp(argv[1], "--focus-smoke") == 0;
        host.releaseSmoke = argc == 2 && strcmp(argv[1], "--release-smoke") == 0;
        host.releaseResultSmoke = argc == 2 && strcmp(argv[1], "--release-result-smoke") == 0;
        host.releaseResultMode = host.releaseResultSmoke || (argc == 2 && strcmp(argv[1], "--release-result") == 0);
        host.smoke = host.firstUseRecoverySmoke || host.firstInstallSmoke || host.consumerSmoke || host.failureSmoke || host.focusSmoke || host.releaseSmoke || host.releaseResultSmoke || (argc == 2 && strcmp(argv[1], "--smoke") == 0);
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



def native_release_request(value) -> dict | None:
    """Finite terminal host intent; neither a URL nor a task command."""
    if type(value) is not dict or type(value.get("action")) is not str:
        return None
    if value == {"action": "restore"}:
        return {"action": "restore"}
    if set(value) != {"action", "distribution"} or value["action"] != "update":
        return None
    candidate = value["distribution"]
    if (type(candidate) is not str or not candidate or len(candidate) > 2048
            or any(ord(char) < 32 or ord(char) == 127 for char in candidate)
            or not Path(candidate).is_absolute()):
        return None
    return {"action": "update", "distribution": candidate}


class NativePresenter:
    """One trusted native window, no fallback and no credential report."""
    def __init__(self, directory: str | Path, *, consumer_smoke: bool = False,
                 ownership_fd: int | None = None):
        if type(consumer_smoke) is not bool:
            raise ValueError("native_host_mode_invalid")
        if ownership_fd is not None and (type(ownership_fd) is not int or ownership_fd < 0):
            raise ValueError("native_host_ownership_invalid")
        self.ownership_fd = ownership_fd
        self.consumer_smoke = consumer_smoke
        self.directory = Path(directory).absolute()
        self.process = None
        self.sequence = 0
        self.current_surface = None
        self._command_lock = threading.RLock()
        self._release_request = None
        self._closed_for_release = False

    def __repr__(self) -> str:
        return "<NativePresenter credential_in_report=False>"

    def __call__(self, surface: ConsumerSurface) -> bool:
        with self._command_lock:
            return self._present_surface(surface)

    def focus(self) -> bool:
        """Focus only the current owned surface; never create or reload one."""
        with self._command_lock:
            if (self.current_surface is None or self.process is None
                    or self.process.poll() is not None):
                return False
            return self._present_surface(self.current_surface, focus_required=True)

    def _present_surface(self, surface: ConsumerSurface, *, focus_required=False) -> bool:
        try:
            self.sequence += 1
            command = native_command(surface, self.sequence)
            if sys.platform != "darwin" or not verify_native_host(self.directory):
                return False
            reuse = self.process is not None and self.process.poll() is None
            focus_only = reuse and self.current_surface == surface
            # A child can exit during image verification. A focus request must
            # NEVER turn that race into cold-start replay of a consumed ticket.
            if focus_required and not focus_only:
                return False
            if focus_only:
                # The exact same in-memory capability has already been
                # presented. Reopening must not consume it again or reload edits.
                command = {"command": "focus", "request": self.sequence}
            if not reuse:
                self._release_request = None
                self._closed_for_release = False
                self.current_surface = None
                self.process = subprocess.Popen(
                    [str(self.directory / "AIApplicationWindow"),
                     *(["--consumer-smoke"] if self.consumer_smoke else [])],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, text=True, bufsize=1,
                    pass_fds=(() if self.ownership_fd is None else (self.ownership_fd,)))
            self.process.stdin.write(json.dumps(command, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
            ready, _, _ = select.select([self.process.stdout], [], [], 5)
            if not ready:
                self.close()
                return False
            reply = json.loads(self.process.stdout.readline(4096))
            if (type(reply) is dict and set(reply) == {"ok", "release_request"}
                    and reply["ok"] is True
                    and native_release_request(reply["release_request"]) is not None):
                # A user can close for release while an owned focus ACK waits.
                # Preserve the terminal event, but do not call it a focus ACK.
                self._release_request = native_release_request(reply["release_request"])
                return False
            expected = {"ok": True, "request": self.sequence,
                        "surface": surface.surface, "window_count": 1}
            if focus_only:
                expected["focus_only"] = True
            accepted = (isinstance(reply, dict)
                        and type(reply.get("request")) is int
                        and type(reply.get("window_count")) is int
                        and reply == expected
                        and (not focus_only or reply.get("focus_only") is True))
            if accepted:
                self.current_surface = surface
            else:
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
            with self._command_lock:
                raw = process.stdout.read(8193)
                if raw:
                    if len(raw) > 8192 or self._release_request is not None:
                        self._release_request = None
                        return False
                    reply = json.loads(raw)
                    if (type(reply) is not dict or set(reply) != {"ok", "release_request"}
                            or reply["ok"] is not True):
                        return False
                    self._release_request = native_release_request(reply["release_request"])
                    if self._release_request is None:
                        return False
                self._closed_for_release = closed and self._release_request is not None
            return closed
        except (OSError, ValueError, TypeError, subprocess.SubprocessError):
            return False
        finally:
            self.close()

    def take_release_request(self) -> dict | None:
        with self._command_lock:
            if not self._closed_for_release:
                return None
            request, self._release_request = self._release_request, None
            self._closed_for_release = False
            return request

    def close(self) -> None:
        with self._command_lock:
            self._close()

    def _close(self) -> None:
        process, self.process = self.process, None
        self.current_surface = None
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


def present_native_release_result(directory: str | Path, result: str, *,
                                  ownership_fd: int | None = None,
                                  smoke: bool = False) -> bool:
    """Verified fixed local result only; no route, service or retry authority."""
    if (type(result) is not str or result not in {"not-confirmed", "reopen-failed"}
            or type(smoke) is not bool
            or ownership_fd is not None and (type(ownership_fd) is not int or ownership_fd < 0)):
        return False
    try:
        root = Path(directory).absolute()
        if sys.platform != "darwin" or not verify_native_host(root):
            return False
        presenter = NativePresenter(root, ownership_fd=ownership_fd)
        try:
            presenter.process = subprocess.Popen(
                [str(root / "AIApplicationWindow"),
                 "--release-result-smoke" if smoke else "--release-result"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1,
                pass_fds=(() if ownership_fd is None else (ownership_fd,)))
            process = presenter.process
            process.stdin.write(json.dumps({"command": "release-result", "result": result}) + "\n")
            process.stdin.flush()
            ready, _, _ = select.select([process.stdout], [], [], 5)
            if not ready:
                return False
            reply = json.loads(process.stdout.readline(4096))
            expected = {"ok": True, "release_result_visible": True,
                        "window_count": 1, "no_automatic_retry": True}
            if (type(reply) is not dict or reply != expected
                    or reply.get("ok") is not True
                    or reply.get("release_result_visible") is not True
                    or reply.get("no_automatic_retry") is not True
                    or type(reply.get("window_count")) is not int):
                return False
            # Normal lifetime follows the explicit human window close. The
            # cloud-only oracle has a finite deadline and emits no private data.
            closed = process.wait(timeout=20) == 0 if smoke else process.wait() == 0
            return closed and process.stdout.read(4097) == ""
        finally:
            presenter.close()
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        return False


def native_first_install_choice(value) -> dict | None:
    """One finite native intent. A selected path adds only a continuity check."""
    if type(value) is not dict:
        return None
    if value == {"action": "cancel"}:
        return {"action": "cancel"}
    if value == {"action": "install", "continuity": "first_use"}:
        return {"action": "install", "continuity": "first_use"}
    if (set(value) != {"action", "continuity", "legacy_directory"}
            or value.get("action") != "install"
            or value.get("continuity") != "selected_legacy_directory"):
        return None
    path = value["legacy_directory"]
    if (type(path) is not str or not path or len(path) > 2048
            or any(ord(char) < 32 or ord(char) == 127 for char in path)
            or not Path(path).is_absolute()):
        return None
    return {"action": "install", "continuity": "selected_legacy_directory", "legacy_directory": path}


def native_first_use_recovery_choice(value) -> dict | None:
    """Recovery has its own finite intent; installation intent cannot grant it."""
    if type(value) is dict and value in ({"action": "cancel"}, {"action": "resume_first_use"}):
        return dict(value)
    return None


def present_native_first_use_recovery(directory: str | Path, *, smoke: bool = False) -> dict | None:
    return _present_first_install_prompt(directory, smoke=smoke, recovery=True)


def present_native_installer(directory: str | Path, mode: str, *, smoke: bool = False) -> dict | None:
    return _present_first_install_prompt(directory, smoke=smoke, installer=mode)


def native_installer_choice(value, mode):
    if (mode not in {"open", "update", "recovery"} or type(value) is not dict
            or set(value) != {"action"} or type(value["action"]) is not str):
        return None
    allowed = {"open", "cancel", "update"} if mode == "update" else {"open", "cancel"}
    return dict(value) if value["action"] in allowed else None


def present_native_first_install(directory: str | Path, *, smoke: bool = False,
                                 result: str | None = None) -> dict | None:
    return _present_first_install_prompt(directory, smoke=smoke, result=result)


def _present_first_install_prompt(directory: str | Path, *, smoke: bool = False,
                                  result: str | None = None, recovery: bool = False,
                                  installer: str | None = None) -> dict | None:
    """Trusted static host, bounded terminal reply; never launches an installer."""
    if (installer is not None and (type(installer) is not str or installer not in {"open", "update", "recovery"}
            or result is not None or recovery)
            or type(smoke) is not bool or type(recovery) is not bool or (smoke or recovery) and result is not None
            or result is not None and (type(result) is not str or result not in {
            "legacy-state-required", "target-occupied", "state-continuity-required",
            "not-confirmed", "reopen-failed", "app-in-use"})):
        return None
    try:
        root = Path(directory).absolute()
        if sys.platform != "darwin" or not verify_native_host(root):
            return None
        presenter = NativePresenter(root)
        try:
            mode = "installer-" + installer if installer else "first-use-recovery" if recovery else "first-install"
            args = (["--first-install-result", result] if result is not None else
                    ["--" + mode + ("-smoke" if smoke else "")])
            presenter.process = subprocess.Popen([str(root / "AIApplicationWindow"), *args],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1, close_fds=True)
            process = presenter.process
            deadline = time.monotonic() + (20 if smoke else 600)
            data = bytearray()
            fd = process.stdout.fileno()
            os.set_blocking(fd, False)
            while b"\n" not in data:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or len(data) >= 8192:
                    return None
                ready, _, _ = select.select([fd], [], [], remaining)
                if not ready:
                    return None
                chunk = os.read(fd, min(1024, 8193 - len(data)))
                if not chunk:
                    return None
                data.extend(chunk)
            if len(data) > 8192 or process.wait(timeout=max(.001, min(5, deadline-time.monotonic()))) != 0:
                return None
            if os.read(fd, 8193) != b"" or not verify_native_host(root):
                return None
            line = data.decode("utf-8")
            if not line.endswith("\n") or line.count("\n") != 1:
                return None
            def unique(pairs):
                output = {}
                for key, value in pairs:
                    if key in output:
                        raise ValueError("duplicate native reply")
                    output[key] = value
                return output
            reply = json.loads(line, object_pairs_hook=unique)
            prefix = "installer" if installer else "first_use_recovery" if recovery else "first_install"
            expected = {"ok", prefix + "_choice"} | ({prefix + "_prompt_visible"} if smoke else set())
            if (type(reply) is not dict or set(reply) != expected or reply.get("ok") is not True
                    or smoke and reply.get(prefix + "_prompt_visible") is not True):
                return None
            validate = native_first_use_recovery_choice if recovery else native_first_install_choice
            return (native_installer_choice(reply[prefix + "_choice"], installer) if installer
                    else validate(reply[prefix + "_choice"]))
        finally:
            presenter.close()
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        return None
