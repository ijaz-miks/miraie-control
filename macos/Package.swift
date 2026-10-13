// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "MirAIeControl",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "MirAIeControl", targets: ["MirAIeControl"])],
    targets: [
        .target(name: "MirAIeKit"),
        .executableTarget(name: "MirAIeControl", dependencies: ["MirAIeKit"]),
        .testTarget(name: "MirAIeKitTests", dependencies: ["MirAIeKit"])
    ]
)
