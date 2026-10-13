// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "MirAIeControl",
    platforms: [.macOS(.v14)],
    products: [.library(name: "MirAIeKit", targets: ["MirAIeKit"])],
    targets: [
        .target(name: "MirAIeKit"),
        .testTarget(name: "MirAIeKitTests", dependencies: ["MirAIeKit"])
    ]
)
