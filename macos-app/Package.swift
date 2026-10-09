// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "Autocode",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "Autocode",
            path: "Sources/Autocode",
            swiftSettings: [.swiftLanguageMode(.v5)]
        )
    ]
)
