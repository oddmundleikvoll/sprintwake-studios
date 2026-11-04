#import <AppKit/AppKit.h>
#import <Sparkle/Sparkle.h>

@interface SWUpdater : NSObject <SPUUpdaterDelegate>
@property(nonatomic,strong) SPUStandardUpdaterController *controller;
@property(nonatomic,strong) id previousDelegate;
@property(nonatomic,copy) void (^installation)(void);
@property(nonatomic,copy) NSString *stage;
@property(nonatomic,copy) NSString *message;
@property(nonatomic,copy) NSString *version;
@property(nonatomic) NSInteger build;
@property(nonatomic) uint64_t requestID;
@property(nonatomic) BOOL approved;
@property(nonatomic) BOOL terminating;
@end

@implementation SWUpdater
- (BOOL)respondsToSelector:(SEL)selector {
    return [super respondsToSelector:selector] || [self.previousDelegate respondsToSelector:selector];
}
- (id)forwardingTargetForSelector:(SEL)selector {
    return [self.previousDelegate respondsToSelector:selector] ? self.previousDelegate : [super forwardingTargetForSelector:selector];
}
- (void)waitWithHandler:(void (^)(void))handler {
    self.installation=handler;self.approved=NO;self.requestID++;
    self.stage=@"waiting";self.message=@"The update is waiting for active jobs and saves to finish.";
}
- (BOOL)updater:(SPUUpdater *)updater shouldPostponeRelaunchForUpdate:(SUAppcastItem *)item untilInvokingBlock:(void (^)(void))handler {
    (void)updater;(void)item;if (self.approved) return NO;[self waitWithHandler:handler];return YES;
}
- (BOOL)updater:(SPUUpdater *)updater willInstallUpdateOnQuit:(SUAppcastItem *)item immediateInstallationBlock:(void (^)(void))handler {
    (void)updater;(void)item;if (self.approved) return NO;self.installation=handler;self.approved=NO;
    self.stage=@"staged";self.message=@"Update downloaded. Install when work is finished.";
    return YES;
}
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)sender {
    if (self.approved) return NSTerminateNow;
    if (self.installation || self.terminating || [self.stage isEqual:@"installing"]) {
        if (![self.stage isEqual:@"waiting"]) { self.requestID++;self.stage=@"waiting"; }
        self.terminating=YES;self.message=@"Waiting for the production engine to stop safely.";
        return NSTerminateCancel;
    }
    if ([self.previousDelegate respondsToSelector:_cmd]) return [self.previousDelegate applicationShouldTerminate:sender];
    return NSTerminateNow;
}
- (void)updater:(SPUUpdater *)updater didFindValidUpdate:(SUAppcastItem *)item {
    (void)updater;self.version=item.displayVersionString;self.build=item.versionString.integerValue;self.stage=@"available";
    self.message=[NSString stringWithFormat:@"Version %@ is available.",self.version];
}
- (void)updater:(SPUUpdater *)updater didDownloadUpdate:(SUAppcastItem *)item {
    (void)updater;(void)item;self.message=@"Update downloaded and preparing.";
}
- (void)updater:(SPUUpdater *)updater didExtractUpdate:(SUAppcastItem *)item {
    (void)updater;(void)item;self.message=@"Update is ready. Confirm installation in the update window.";
}
- (void)updater:(SPUUpdater *)updater willInstallUpdate:(SUAppcastItem *)item {
    (void)updater;(void)item;
    if (!self.approved && !self.installation) {self.stage=@"installing";self.terminating=YES;}
}
- (void)updater:(SPUUpdater *)updater didAbortWithError:(NSError *)error {
    (void)updater;self.installation=nil;self.terminating=NO;self.approved=NO;
    self.stage=error.code==SUNoUpdateError ? @"idle" : @"error";
    self.message=error.code==SUNoUpdateError ? @"Du har den nyeste tilgjengelige utgaven." : error.localizedDescription;
}
@end

static SWUpdater *bridge;

int sw_update_start(void) {
    if (bridge) return bridge.controller!=nil;
    bridge=[SWUpdater new];bridge.stage=@"disabled";bridge.message=@"Updates are available in the packaged app.";bridge.version=@"";
    NSDictionary *info=NSBundle.mainBundle.infoDictionary;
    NSString *feed=info[@"SUFeedURL"],*key=info[@"SUPublicEDKey"];
    NSURL *url=feed ? [NSURL URLWithString:feed] : nil;
    BOOL localTest=[info[@"SprintwakeUpdateTest"] boolValue] && [@[@"localhost",@"127.0.0.1",@"::1"] containsObject:url.host];
    if (!url || (!localTest && ![url.scheme isEqual:@"https"]) || key.length!=44) return 0;
    bridge.previousDelegate=NSApp.delegate;NSApp.delegate=(id<NSApplicationDelegate>)bridge;
    bridge.controller=[[SPUStandardUpdaterController alloc] initWithStartingUpdater:NO updaterDelegate:bridge userDriverDelegate:nil];
    bridge.stage=@"idle";bridge.message=@"Check for a new signed version of Sprintwake Studios.";
    [bridge.controller startUpdater];
    return 1;
}
void sw_update_check(void) {
    if (!bridge.controller || bridge.installation) return;
    if (!bridge.controller.updater.sessionInProgress) {bridge.stage=@"checking";bridge.message=@"Checking for updates…";}
    [bridge.controller checkForUpdates:nil];
}
void sw_update_request_install(void) {
    if (!bridge || (!bridge.installation && !bridge.terminating)) return;
    bridge.requestID++;bridge.stage=@"waiting";bridge.message=@"Waiting for active productions and saves…";
}
void sw_update_defer(void) {
    if (!bridge.approved && bridge.installation) {bridge.stage=@"staged";bridge.message=@"Update is ready for later installation.";}
}
int sw_update_approve(uint64_t requestID) {
    if (!bridge || bridge.requestID!=requestID || ![bridge.stage isEqual:@"waiting"]) return 0;
    bridge.approved=YES;bridge.stage=@"installing";bridge.message=@"Installing and restarting…";
    void (^handler)(void)=bridge.installation;bridge.installation=nil;
    if (handler) handler();else [NSApp terminate:nil];
    return 1;
}
const char *sw_update_status(void) {
    static char *json=NULL;free(json);
    NSDictionary *value=@{@"configured":@(bridge.controller!=nil),@"stage":bridge.stage?:@"disabled",@"message":bridge.message?:@"",@"version":bridge.version?:@"",@"build":@(bridge.build),@"requestId":@(bridge.requestID)};
    NSData *data=[NSJSONSerialization dataWithJSONObject:value options:0 error:NULL];
    json=strdup([[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding].UTF8String);
    return json;
}
