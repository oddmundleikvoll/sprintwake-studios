#import <AppKit/AppKit.h>
// Exact geometry from Sprintwake Projects. Studios uses charcoal/plum, copper and gold.
int main(int argc,const char **argv) {
    if (argc<2 || argc>3) return 2;
    @autoreleasepool {
        BOOL mark=argc==3;
        NSBitmapImageRep *bitmap=[[NSBitmapImageRep alloc] initWithBitmapDataPlanes:NULL pixelsWide:1024 pixelsHigh:1024 bitsPerSample:8 samplesPerPixel:4 hasAlpha:YES isPlanar:NO colorSpaceName:NSDeviceRGBColorSpace bytesPerRow:0 bitsPerPixel:0];
        NSGraphicsContext *context=[NSGraphicsContext graphicsContextWithBitmapImageRep:bitmap];
        [NSGraphicsContext saveGraphicsState];NSGraphicsContext.currentContext=context;
        CGContextRef c=context.CGContext;CGContextTranslateCTM(c,0,1024);CGContextScaleCTM(c,1,-1);
        if (!mark) {
            NSBezierPath *tile=[NSBezierPath bezierPathWithRoundedRect:NSMakeRect(62,62,900,900) xRadius:198 yRadius:198];
            NSGradient *gradient=[[NSGradient alloc] initWithStartingColor:[NSColor colorWithRed:.22 green:.17 blue:.22 alpha:1] endingColor:[NSColor colorWithRed:.055 green:.09 blue:.10 alpha:1]];
            [gradient drawInBezierPath:tile angle:45];
        }
        CGContextSetRGBStrokeColor(c,.922,.643,.467,1);CGContextSetLineWidth(c,108);CGContextSetLineCap(c,kCGLineCapRound);CGContextSetLineJoin(c,kCGLineJoinRound);
        CGContextMoveToPoint(c,700,300);CGContextAddLineToPoint(c,430,300);
        CGContextAddCurveToPoint(c,350,300,300,346,300,406);CGContextAddCurveToPoint(c,300,466,350,512,430,512);
        CGContextAddLineToPoint(c,594,512);CGContextAddCurveToPoint(c,674,512,724,558,724,618);
        CGContextAddCurveToPoint(c,724,678,674,724,594,724);CGContextAddLineToPoint(c,324,724);CGContextStrokePath(c);
        CGContextSetRGBFillColor(c,.957,.835,.627,1);CGContextFillEllipseInRect(c,CGRectMake(632.5,232.5,135,135));
        [NSGraphicsContext restoreGraphicsState];
        NSData *png=[bitmap representationUsingType:NSBitmapImageFileTypePNG properties:@{}];
        if (![png writeToFile:[NSString stringWithUTF8String:argv[1]] atomically:YES]) return 1;
    }
    return 0;
}
