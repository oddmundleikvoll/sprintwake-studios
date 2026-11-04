#import <Foundation/Foundation.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

// All executable code lives in this app; user data and logs live outside it.
int main(int argc,char **argv) {
    @autoreleasepool {
        NSString *bundle=NSBundle.mainBundle.bundlePath.stringByResolvingSymlinksInPath;
        NSString *root=[bundle stringByAppendingPathComponent:@"Contents/Resources/Studio"];
        NSData *data=[NSData dataWithContentsOfFile:[root stringByAppendingPathComponent:@"runtime/python.json"]];
        NSDictionary *config=data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:NULL] : nil;
        if (!config) {fprintf(stderr,"Sprintwake Studios runtime is missing.\n");return 1;}
        NSString *(^resolve)(NSString *)=^NSString *(NSString *key) {
            NSString *relative=config[key];
            return [[root stringByAppendingPathComponent:relative] stringByStandardizingPath];
        };
        NSString *pythonHome=resolve(@"base"),*executable=resolve(@"executable"),*library=resolve(@"library");
        NSString *contents=[bundle stringByAppendingPathComponent:@"Contents/"];
        for (NSString *path in @[pythonHome,executable,library]) {
            if (![path hasPrefix:contents] || ![NSFileManager.defaultManager fileExistsAtPath:path]) {fprintf(stderr,"Invalid embedded Python runtime.\n");return 1;}
        }
        NSString *logDir=[NSHomeDirectory() stringByAppendingPathComponent:@"Library/Logs/Sprintwake Studios"];
        if ([NSBundle.mainBundle.infoDictionary[@"SprintwakeUpdateTest"] boolValue]) logDir=[NSBundle.mainBundle.infoDictionary[@"SprintwakeQARoot"] stringByAppendingPathComponent:@"logs"];
        [NSFileManager.defaultManager createDirectoryAtPath:logDir withIntermediateDirectories:YES attributes:nil error:NULL];
        freopen([[logDir stringByAppendingPathComponent:@"app.log"] fileSystemRepresentation],"a",stderr);
        chdir(root.fileSystemRepresentation);
        setenv("PYTHONHOME",pythonHome.UTF8String,1);
        setenv("PYTHONPATH",[[NSString stringWithFormat:@"%@:%@",root,[root stringByAppendingPathComponent:@"runtime/Raven/bindings/python"]] UTF8String],1);
        setenv("PYTHONDONTWRITEBYTECODE","1",1);
        setenv("PYTHONNOUSERSITE","1",1);
        setenv("SPRINTWAKE_APP_BUNDLE",bundle.UTF8String,1);
        setenv("SPRINTWAKE_BUNDLED_PYTHON",executable.UTF8String,1);
        setenv("SPRINTWAKE_APP_BUILD",[NSBundle.mainBundle.infoDictionary[@"CFBundleVersion"] UTF8String],1);
        if ([NSBundle.mainBundle.infoDictionary[@"SprintwakeUpdateTest"] boolValue]) {
            NSString *qaRoot=NSBundle.mainBundle.infoDictionary[@"SprintwakeQARoot"];
            if (qaRoot.length>0) setenv("SPRINTWAKE_QA_ROOT",qaRoot.UTF8String,1);
        }
        setenv("RAVEN_LIBRARY",[[root stringByAppendingPathComponent:@"runtime/build/libraven.dylib"] UTF8String],1);
        setenv("MVK_CONFIG_LOG_LEVEL","1",1);
        void *molten=dlopen([[root stringByAppendingPathComponent:@"runtime/MoltenVK/MoltenVK/dynamic/dylib/macOS/libMoltenVK.dylib"] fileSystemRepresentation],RTLD_NOW|RTLD_GLOBAL);
        if (!molten) {fprintf(stderr,"MoltenVK: %s\n",dlerror());return 1;}
        void *python=dlopen(library.fileSystemRepresentation,RTLD_NOW|RTLD_GLOBAL);
        if (!python) {fprintf(stderr,"Python: %s\n",dlerror());return 1;}
        int (*pythonMain)(int,char **)=(int (*)(int,char **))dlsym(python,"Py_BytesMain");
        if (!pythonMain) return 1;
        char **args=calloc((size_t)argc+3,sizeof(char *));if (!args) return 1;
        args[0]=strdup(executable.UTF8String);args[1]=strdup("-B");args[2]=strdup([[root stringByAppendingPathComponent:@"studio.py"] UTF8String]);
        for (int i=1;i<argc;i++) args[i+2]=argv[i];
        int result=pythonMain(argc+2,args);free(args[0]);free(args[1]);free(args[2]);free(args);return result;
    }
}
