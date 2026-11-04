#import <AppKit/AppKit.h>
#import <AVFoundation/AVFoundation.h>
#import <CoreVideo/CoreVideo.h>
#include <math.h>
#include <stdint.h>

// A single UI-thread player. AVPlayer owns the audio clock; Raven gets RGBA frames.
static AVPlayer *player;
static AVPlayerItemVideoOutput *output;
static uint8_t *rgba;
static size_t capacity;
static char *dialogPath;
static char errorText[1024];
static int wantedFrame;
static int hasAudio;
static int menuAction;

void sr_attach_window(void *handle) {
    NSWindow *window = (__bridge NSWindow *)handle;
    if (!window || ![window isKindOfClass:[NSWindow class]]) return;
    [window makeKeyAndOrderFront:nil];
    [window makeFirstResponder:window.contentView];
}

@interface SRMenuTarget : NSObject
- (void)invoke:(NSMenuItem *)sender;
@end
@implementation SRMenuTarget
- (void)invoke:(NSMenuItem *)sender { menuAction = (int)sender.tag; }
@end
static SRMenuTarget *menuTarget;

static void sr_add_menu(NSMenu *menu, NSString *title, NSString *key, int tag, BOOL shift) {
    NSMenuItem *item = [[NSMenuItem alloc] initWithTitle:title action:@selector(invoke:) keyEquivalent:key];
    item.target = menuTarget; item.tag = tag;
    item.keyEquivalentModifierMask = NSEventModifierFlagCommand | (shift ? NSEventModifierFlagShift : 0);
    [menu addItem:item];
}

void sr_prepare(void) {
    [[NSProcessInfo processInfo] setProcessName:@"Sprintwake Studios"];
    [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
    menuTarget = [SRMenuTarget new];
    NSMenu *main = [[NSMenu alloc] initWithTitle:@"Sprintwake Studios"];
    NSArray *titles = @[@"Sprintwake Studios", @"File", @"Edit"];
    NSMutableArray *menus = [NSMutableArray array];
    for (NSString *title in titles) {
        NSMenuItem *item = [[NSMenuItem alloc] initWithTitle:title action:NULL keyEquivalent:@""];
        NSMenu *submenu = [[NSMenu alloc] initWithTitle:title];
        item.submenu = submenu; [main addItem:item]; [menus addObject:submenu];
    }
    sr_add_menu(menus[0], @"Check for Updates…", @"", 11, NO);
    sr_add_menu(menus[0], @"Quit Sprintwake Studios", @"q", 1, NO);
    sr_add_menu(menus[1], @"Open Project…", @"o", 2, NO);
    sr_add_menu(menus[1], @"New Project…", @"n", 10, NO);
    sr_add_menu(menus[1], @"Save Project", @"s", 3, NO);
    sr_add_menu(menus[2], @"Undo", @"z", 8, NO);
    sr_add_menu(menus[2], @"Redo", @"z", 9, YES);
    [menus[2] addItem:[NSMenuItem separatorItem]];
    sr_add_menu(menus[2], @"Klipp ut", @"x", 7, NO);
    sr_add_menu(menus[2], @"Kopier", @"c", 5, NO);
    sr_add_menu(menus[2], @"Lim inn", @"v", 6, NO);
    sr_add_menu(menus[2], @"Marker alt", @"a", 4, NO);
    [NSApp setMainMenu:main];
    [NSApp activateIgnoringOtherApps:YES];
}
int sr_menu_action(void) { int result = menuAction; menuAction = 0; return result; }

void sr_close(void) {
    [player pause];
    [player replaceCurrentItemWithPlayerItem:nil];
    player = nil;
    output = nil;
    free(rgba);
    rgba = NULL;
    capacity = 0;
    errorText[0] = 0;
    hasAudio = 0;
}

int sr_open(const char *path) {
    sr_close();
    @autoreleasepool {
        NSURL *url = [NSURL fileURLWithPath:[NSString stringWithUTF8String:path]];
        if (![[NSFileManager defaultManager] fileExistsAtPath:url.path]) {
            snprintf(errorText, sizeof(errorText), "Videofilen finnes ikke.");
            return 0;
        }
        AVPlayerItem *item = [AVPlayerItem playerItemWithURL:url];
        output = [[AVPlayerItemVideoOutput alloc] initWithPixelBufferAttributes:@{
            (id)kCVPixelBufferPixelFormatTypeKey: @(kCVPixelFormatType_32BGRA),
            (id)kCVPixelBufferMetalCompatibilityKey: @YES
        }];
        [item addOutput:output];
        [item.asset loadTracksWithMediaType:AVMediaTypeAudio completionHandler:^(NSArray<AVAssetTrack *> *tracks, __unused NSError *error) {
            dispatch_async(dispatch_get_main_queue(), ^{ if (player.currentItem == item) hasAudio = tracks.count > 0; });
        }];
        player = [AVPlayer playerWithPlayerItem:item];
        player.actionAtItemEnd = AVPlayerActionAtItemEndPause;
        player.volume = 0.8;
        wantedFrame = 1;
        return 1;
    }
}

int sr_status(void) {
    if (!player) return 0;
    if (player.currentItem.status == AVPlayerItemStatusFailed) {
        snprintf(errorText, sizeof(errorText), "%s", player.currentItem.error.localizedDescription.UTF8String);
        return -1;
    }
    return player.currentItem.status == AVPlayerItemStatusReadyToPlay ? 1 : 0;
}
const char *sr_error(void) { return errorText; }
double sr_time(void) {
    double t = player ? CMTimeGetSeconds(player.currentTime) : 0;
    return isfinite(t) ? t : 0;
}
double sr_duration(void) {
    double t = player ? CMTimeGetSeconds(player.currentItem.duration) : 0;
    return isfinite(t) && t > 0 ? t : 0;
}
int sr_playing(void) { return player && player.rate > 0; }
void sr_play(int playing) { if (playing) [player play]; else [player pause]; }
void sr_mute(int muted) { player.muted = muted != 0; }
int sr_has_audio(void) {
    return hasAudio;
}
void sr_seek(double seconds) {
    if (!isfinite(seconds) || seconds < 0) return;
    CMTime time = CMTimeMakeWithSeconds(seconds, 600);
    [player seekToTime:time toleranceBefore:kCMTimeZero toleranceAfter:kCMTimeZero
      completionHandler:^(BOOL finished) { if (finished) wantedFrame = 1; }];
}

uint8_t *sr_frame(int *width, int *height) {
    if (sr_status() != 1) return NULL;
    @autoreleasepool {
        CMTime time = player.currentTime;
        if (![output hasNewPixelBufferForItemTime:time] && !wantedFrame) return NULL;
        CVPixelBufferRef pixel = [output copyPixelBufferForItemTime:time itemTimeForDisplay:NULL];
        if (!pixel) return NULL;
        size_t w = CVPixelBufferGetWidth(pixel), h = CVPixelBufferGetHeight(pixel);
        if (w > 4096 || h > 4096) {
            CVPixelBufferRelease(pixel);
            snprintf(errorText, sizeof(errorText), "Player supports video up to 4096 pixels per side.");
            return NULL;
        }
        size_t bytes = w * h * 4;
        if (bytes > capacity) {
            uint8_t *next = realloc(rgba, bytes);
            if (!next) { CVPixelBufferRelease(pixel); return NULL; }
            rgba = next; capacity = bytes;
        }
        CVPixelBufferLockBaseAddress(pixel, kCVPixelBufferLock_ReadOnly);
        const uint8_t *src = CVPixelBufferGetBaseAddress(pixel);
        size_t stride = CVPixelBufferGetBytesPerRow(pixel);
        for (size_t y = 0; y < h; y++) {
            for (size_t x = 0; x < w; x++) {
                size_t s = y * stride + x * 4, d = (y * w + x) * 4;
                rgba[d] = src[s + 2]; rgba[d + 1] = src[s + 1];
                rgba[d + 2] = src[s]; rgba[d + 3] = 255;
            }
        }
        CVPixelBufferUnlockBaseAddress(pixel, kCVPixelBufferLock_ReadOnly);
        CVPixelBufferRelease(pixel);
        *width = (int)w; *height = (int)h; wantedFrame = 0;
        return rgba;
    }
}

const char *sr_choose_project(void) {
    free(dialogPath); dialogPath = NULL;
    NSOpenPanel *panel = [NSOpenPanel openPanel];
    panel.title = @"Open a Storyboard or Raven workspace copy";
    panel.message = @"The original project is read-only. Changes are saved in Raven's own folder.";
    panel.canChooseDirectories = YES;
    panel.canChooseFiles = YES;
    panel.allowsMultipleSelection = NO;
    if ([panel runModal] == NSModalResponseOK) dialogPath = strdup(panel.URL.path.UTF8String);
    return dialogPath;
}

const char *sr_choose_media(void) {
    free(dialogPath); dialogPath = NULL;
    NSOpenPanel *panel = [NSOpenPanel openPanel];
    panel.title = @"Choose an image, film, or audio recording";
    panel.canChooseDirectories = NO; panel.canChooseFiles = YES;
    panel.allowsMultipleSelection = NO;
    if ([panel runModal] == NSModalResponseOK) dialogPath = strdup(panel.URL.path.UTF8String);
    return dialogPath;
}
void sr_reveal(const char *path) {
    NSURL *url = [NSURL fileURLWithPath:[NSString stringWithUTF8String:path]];
    [[NSWorkspace sharedWorkspace] activateFileViewerSelectingURLs:@[url]];
}
int sr_confirm(const char *title, const char *message) {
    NSAlert *alert = [NSAlert new];
    alert.messageText = [NSString stringWithUTF8String:title];
    alert.informativeText = [NSString stringWithUTF8String:message];
    [alert addButtonWithTitle:@"Continue"]; [alert addButtonWithTitle:@"Cancel"];
    return [alert runModal] == NSAlertFirstButtonReturn;
}

// Provider credentials are local Keychain items, never project/config JSON.
#import <Security/Security.h>
static NSDictionary *sr_secret_query(const char *account) {
    NSString *name = account ? [NSString stringWithUTF8String:account] : @"";
    return @{(__bridge id)kSecClass:(__bridge id)kSecClassGenericPassword,
             (__bridge id)kSecAttrService:@"Raven Studio connections",
             (__bridge id)kSecAttrAccount:name};
}
const char *sr_secret_get(const char *account) {
    static _Thread_local char *buffer = NULL;
    if (buffer) { free(buffer); buffer = NULL; }
    @autoreleasepool {
        NSMutableDictionary *query = [sr_secret_query(account) mutableCopy];
        query[(__bridge id)kSecReturnData] = @YES;
        query[(__bridge id)kSecMatchLimit] = (__bridge id)kSecMatchLimitOne;
        CFTypeRef result = NULL;
        if (SecItemCopyMatching((__bridge CFDictionaryRef)query, &result) != errSecSuccess) return NULL;
        NSData *data = CFBridgingRelease(result);
        NSString *value = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
        if (value) buffer = strdup(value.UTF8String);
    }
    return buffer;
}
int sr_secret_set(const char *account, const char *value) {
    @autoreleasepool {
        if (!account || !value) return 0;
        NSDictionary *query = sr_secret_query(account);
        NSData *data = [[NSString stringWithUTF8String:value] dataUsingEncoding:NSUTF8StringEncoding];
        NSDictionary *attributes = @{(__bridge id)kSecValueData:data};
        OSStatus status = SecItemUpdate((__bridge CFDictionaryRef)query, (__bridge CFDictionaryRef)attributes);
        if (status == errSecItemNotFound) {
            NSMutableDictionary *insert = [query mutableCopy];
            [insert addEntriesFromDictionary:attributes];
            status = SecItemAdd((__bridge CFDictionaryRef)insert, NULL);
        }
        return status == errSecSuccess;
    }
}
int sr_secret_delete(const char *account) {
    @autoreleasepool {
        OSStatus status = SecItemDelete((__bridge CFDictionaryRef)sr_secret_query(account));
        return status == errSecSuccess || status == errSecItemNotFound;
    }
}
