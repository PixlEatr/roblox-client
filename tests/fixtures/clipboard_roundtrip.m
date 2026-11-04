#import <AppKit/AppKit.h>
#import <Foundation/Foundation.h>
// Test only: retain every pasteboard format in memory and restore it afterward.
// Neither original clipboard contents nor the test's contents are logged.
int main(int argc,const char** argv) { @autoreleasepool {
    NSPasteboard* board=NSPasteboard.generalPasteboard;
    NSMutableArray* saved=[NSMutableArray array];
    for(NSPasteboardItem* original in board.pasteboardItems) {
        NSPasteboardItem* item=[NSPasteboardItem new];
        for(NSPasteboardType type in original.types) {
            NSData* data=[original dataForType:type];if(data)[item setData:data forType:type];
        }
        [saved addObject:item];
    }
    BOOL ok=YES;
    @try {
        for(int i=2;i<argc;++i) {
            NSTask* task=[NSTask new];task.executableURL=[NSURL fileURLWithPath:@(argv[1])];
            task.arguments=@[@"--source",@(argv[i]),@"--timeout-ms",@"2000"];
            task.standardOutput=NSFileHandle.fileHandleWithNullDevice;
            task.standardError=NSFileHandle.fileHandleWithNullDevice;
            NSError* error=nil;
            if(![task launchAndReturnError:&error]){ok=NO;break;}
            [task waitUntilExit];
            if(task.terminationStatus!=0 || ![[board stringForType:NSPasteboardTypeString] isEqualToString:@"UNC clipboard λ\nroundtrip"]){ok=NO;break;}
        }
    } @finally {
        [board clearContents];if(saved.count && ![board writeObjects:saved])ok=NO;
    }
    puts(ok?"clipboard roundtrip passed; previous formats restored":"clipboard roundtrip failed");
    return ok?0:1;
} }
