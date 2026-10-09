// Renders the Autopilot app icon (1024px master): an attitude indicator
// (artificial horizon) instrument — the universal autopilot symbol.
// Dark avionics panel, sky-over-earth dial, amber aircraft wings marker.
// Usage: swift gen-icon.swift /path/to/output.png
import Foundation
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers

let output = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "icon-master.png"
let size = 1024

guard let ctx = CGContext(
    data: nil, width: size, height: size, bitsPerComponent: 8, bytesPerRow: 0,
    space: CGColorSpace(name: CGColorSpace.sRGB)!,
    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
) else {
    fatalError("cannot create context")
}
ctx.setAllowsAntialiasing(true)
ctx.setShouldAntialias(true)

func linearGradient(_ start: CGPoint, _ end: CGPoint, _ colors: [CGColor]) -> CGGradient {
    CGGradient(colorsSpace: CGColorSpace(name: CGColorSpace.sRGB)!, colors: colors as CFArray, locations: nil)!
}

// Squircle canvas; corners transparent.
let inset: CGFloat = 100
let body = CGRect(x: inset, y: inset, width: CGFloat(size) - 2 * inset, height: CGFloat(size) - 2 * inset)
let squircle = CGPath(roundedRect: body, cornerWidth: 186, cornerHeight: 186, transform: nil)

// 1. Avionics-panel background: deep slate with a faint radial glow behind the dial.
ctx.saveGState()
ctx.addPath(squircle)
ctx.clip()
ctx.drawLinearGradient(
    linearGradient(CGPoint(x: 0, y: 0), CGPoint(x: 0, y: 1024),
                   [CGColor(red: 0.13, green: 0.17, blue: 0.26, alpha: 1),
                    CGColor(red: 0.05, green: 0.07, blue: 0.12, alpha: 1)]),
    start: CGPoint(x: 0, y: 1024), end: CGPoint(x: 0, y: 0), options: [])
let glow = CGGradient(colorsSpace: CGColorSpace(name: CGColorSpace.sRGB)!,
                      colors: [CGColor(red: 0.35, green: 0.68, blue: 0.95, alpha: 0.35),
                               CGColor(red: 0.35, green: 0.68, blue: 0.95, alpha: 0)] as CFArray,
                      locations: [0, 1])!
ctx.drawRadialGradient(glow, startCenter: CGPoint(x: 512, y: 512), startRadius: 0,
                       endCenter: CGPoint(x: 512, y: 512), endRadius: 470, options: [])
ctx.restoreGState()

// 2. Instrument dial: bezel rings.
let dialCenter = CGPoint(x: 512, y: 512)
let bezelOuter: CGFloat = 340
let dialRadius: CGFloat = 302
ctx.saveGState()
ctx.addPath(squircle)
ctx.clip()
ctx.setStrokeColor(CGColor(red: 0.91, green: 0.93, blue: 0.96, alpha: 1))
ctx.setLineWidth(30)
ctx.strokeEllipse(in: CGRect(x: dialCenter.x - bezelOuter, y: dialCenter.y - bezelOuter, width: bezelOuter * 2, height: bezelOuter * 2))
ctx.setStrokeColor(CGColor(red: 0.10, green: 0.12, blue: 0.18, alpha: 1))
ctx.setLineWidth(14)
ctx.strokeEllipse(in: CGRect(x: dialCenter.x - dialRadius - 7, y: dialCenter.y - dialRadius - 7, width: (dialRadius + 7) * 2, height: (dialRadius + 7) * 2))
ctx.restoreGState()

// 3. Sky-over-earth dial face.
ctx.saveGState()
ctx.addPath(squircle)
ctx.clip()
ctx.addArc(center: dialCenter, radius: dialRadius, startAngle: 0, endAngle: 2 * .pi, clockwise: false)
ctx.clip()
// Sky (upper half), brighter near the top.
ctx.drawLinearGradient(
    linearGradient(CGPoint(x: 0, y: 512), CGPoint(x: 0, y: 814),
                   [CGColor(red: 0.14, green: 0.62, blue: 0.94, alpha: 1),
                    CGColor(red: 0.30, green: 0.76, blue: 0.99, alpha: 1)]),
    start: CGPoint(x: 0, y: 512), end: CGPoint(x: 0, y: 814), options: [])
// Earth (lower half), warmer toward the bottom.
ctx.drawLinearGradient(
    linearGradient(CGPoint(x: 0, y: 512), CGPoint(x: 0, y: 210),
                   [CGColor(red: 0.55, green: 0.36, blue: 0.16, alpha: 1),
                    CGColor(red: 0.38, green: 0.23, blue: 0.09, alpha: 1)]),
    start: CGPoint(x: 0, y: 512), end: CGPoint(x: 0, y: 210), options: [])
// Horizon line.
ctx.setStrokeColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
ctx.setLineWidth(10)
ctx.move(to: CGPoint(x: dialCenter.x - dialRadius, y: 512))
ctx.addLine(to: CGPoint(x: dialCenter.x + dialRadius, y: 512))
ctx.strokePath()
// Pitch ladder marks, shortened as they approach the dial edge so none touch it.
ctx.setLineWidth(9)
ctx.setLineCap(.round)
for (dy, half) in [(CGFloat(96), CGFloat(78)), (CGFloat(186), CGFloat(52))] {
    let chordHalf = sqrt(dialRadius * dialRadius - dy * dy) - 34
    let markHalf = min(half, chordHalf)
    for sign in [1.0, -1.0] {
        let y = 512 + dy * sign
        ctx.move(to: CGPoint(x: dialCenter.x - markHalf, y: y))
        ctx.addLine(to: CGPoint(x: dialCenter.x + markHalf, y: y))
    }
}
ctx.setStrokeColor(CGColor(red: 1, green: 1, blue: 1, alpha: 0.92))
ctx.strokePath()
ctx.restoreGState()

// 4. Amber aircraft symbol (fixed wings marker) with a soft shadow.
ctx.saveGState()
ctx.addPath(squircle)
ctx.clip()
ctx.setShadow(offset: CGSize(width: 0, height: -10), blur: 18, color: CGColor(red: 0, green: 0, blue: 0, alpha: 0.45))
ctx.setFillColor(CGColor(red: 1.0, green: 0.76, blue: 0.15, alpha: 1))
let wing: CGFloat = 24
// Left and right wings with a gap from the center dot.
ctx.fill(CGRect(x: dialCenter.x - 178, y: 512 - wing / 2, width: 118, height: wing))
ctx.fill(CGRect(x: dialCenter.x + 60, y: 512 - wing / 2, width: 118, height: wing))
// Center dot.
ctx.fillEllipse(in: CGRect(x: dialCenter.x - 26, y: 512 - 26, width: 52, height: 52))
ctx.restoreGState()

// 5. Roll pointer at twelve o'clock, on the bezel.
ctx.saveGState()
ctx.addPath(squircle)
ctx.clip()
ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
let pointerTip = CGPoint(x: dialCenter.x, y: dialCenter.y + dialRadius - 26)
let pointer = CGMutablePath()
pointer.move(to: pointerTip)
pointer.addLine(to: CGPoint(x: dialCenter.x - 30, y: dialCenter.y + dialRadius + 40))
pointer.addLine(to: CGPoint(x: dialCenter.x + 30, y: dialCenter.y + dialRadius + 40))
pointer.closeSubpath()
ctx.addPath(pointer)
ctx.fillPath()
ctx.restoreGState()

// 6. Gentle top highlight.
ctx.saveGState()
ctx.addPath(squircle)
ctx.clip()
ctx.drawLinearGradient(
    linearGradient(CGPoint(x: 0, y: 1024), CGPoint(x: 0, y: 620),
                   [CGColor(red: 1, green: 1, blue: 1, alpha: 0.13), CGColor(red: 1, green: 1, blue: 1, alpha: 0)]),
    start: CGPoint(x: 0, y: 1024), end: CGPoint(x: 0, y: 620), options: [])
ctx.restoreGState()

let image = ctx.makeImage()!
let url = URL(fileURLWithPath: output) as CFURL
let dest = CGImageDestinationCreateWithURL(url, UTType.png.identifier as CFString, 1, nil)!
CGImageDestinationAddImage(dest, image, nil)
guard CGImageDestinationFinalize(dest) else { fatalError("png write failed") }
print("wrote \(output)")
