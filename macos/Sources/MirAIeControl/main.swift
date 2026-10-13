import AppKit
import Combine
import SwiftUI
import MirAIeKit

@MainActor final class AppDelegate: NSObject, NSApplicationDelegate, NSPopoverDelegate {
    private var statusItem: NSStatusItem!
    private let popover = NSPopover()
    private var accountWindow: NSWindow?
    private var subscription: AnyCancellable?
    private var refreshTimer: Timer?
    private let demo = CommandLine.arguments.contains("--demo") || CommandLine.arguments.contains("--ui-smoke")
    private lazy var controller = ACController(api: demo ? DemoAPI() : CloudAPI(),
        store: demo ? DemoStore() : KeychainStore(), defaults: demo ? nil : .standard)

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.image = NSImage(systemSymbolName: "snowflake", accessibilityDescription: "MirAIe Control")
        statusItem.button?.image?.isTemplate = true
        statusItem.button?.imagePosition = .imageLeft
        statusItem.button?.target = self
        statusItem.button?.action = #selector(togglePanel)
        statusItem.button?.toolTip = "MirAIe air conditioner controls"
        popover.behavior = CommandLine.arguments.contains("--ui-smoke") ? .applicationDefined : .transient
        popover.animates = !CommandLine.arguments.contains("--ui-smoke")
        popover.delegate = self
        popover.contentSize = NSSize(width: 360, height: 540)
        popover.contentViewController = NSHostingController(rootView: MenuPanel(controller: controller) { [weak self] in self?.showAccount() })
        subscription = controller.objectWillChange.sink { [weak self] in
            DispatchQueue.main.async { self?.updateStatusItem() }
        }
        refreshTimer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            Task { @MainActor in await self?.controller.refresh() }
        }
        Task {
            await controller.start()
            updateStatusItem()
            if CommandLine.arguments.contains("--ui-smoke") { await smokeTest() }
            else if CommandLine.arguments.contains("--show-panel") { statusItem.button?.performClick(nil) }
            else if controller.username.isEmpty { showAccount() }
        }
    }

    @objc private func togglePanel() {
        if popover.isShown { popover.performClose(nil); return }
        guard let button = statusItem.button else { return }
        popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
    }

    private func showAccount() {
        popover.performClose(nil)
        if accountWindow == nil {
            let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 460, height: 450),
                styleMask: [.titled, .closable], backing: .buffered, defer: false)
            window.title = "MirAIe Control Settings"
            window.isReleasedWhenClosed = false
            window.contentViewController = NSHostingController(rootView: AccountView(controller: controller))
            window.center()
            accountWindow = window
        }
        NSApp.activate(ignoringOtherApps: true)
        accountWindow?.makeKeyAndOrderFront(nil)
    }

    private func updateStatusItem() {
        let device = controller.selectedDevice
        if device?.online == true, device?.power == true, let temperature = device?.temperature {
            let title = " " + (JSONValue.number(temperature).text ?? "") + "°"
            if statusItem.button?.title != title { statusItem.button?.title = title }
        } else if statusItem.button?.title != "" { statusItem.button?.title = "" }
        statusItem.button?.setAccessibilityLabel("MirAIe Control" + (device.map { ", \($0.name), \($0.online ? "online" : "offline")" } ?? ""))
    }

    func applicationWillTerminate(_ notification: Notification) {
        refreshTimer?.invalidate()
        subscription?.cancel()
        if let statusItem { NSStatusBar.system.removeStatusItem(statusItem) }
    }

    private func smokeTest() async {
        guard demo, let output = ProcessInfo.processInfo.environment["MIRAIE_UI_TEST_OUTPUT"] else {
            NSApp.terminate(nil); return
        }
        do {
            let directory = URL(fileURLWithPath: output, isDirectory: true)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            try await Task.sleep(nanoseconds: 300_000_000)
            statusItem.button?.performClick(nil)
            try await Task.sleep(nanoseconds: 800_000_000)
            guard popover.isShown else {
                throw MirAIeError("The native menu-bar popover did not appear.")
            }
            guard let view = popover.contentViewController?.view, view.bounds.width > 0,
                  let image = view.bitmapImageRepForCachingDisplay(in: view.bounds) else {
                throw MirAIeError("The native popover could not be captured.")
            }
            view.cacheDisplay(in: view.bounds, to: image)
            if let data = image.representation(using: .png, properties: [:]) {
                try data.write(to: directory.appendingPathComponent("online.png"), options: .withoutOverwriting)
            }
            let original = controller.selectedDevice?.temperature
            await controller.command(.temperature, value: .number(25))
            guard controller.selectedDevice?.temperature == 25, controller.error.isEmpty,
                  original == 24 else { throw MirAIeError("Mock temperature confirmation failed.") }
            controller.selectedID = "demo-ac-2"
            try await Task.sleep(nanoseconds: 300_000_000)
            guard controller.selectedDevice?.online == false, controller.selectedDevice?.power == nil,
                  !controller.canControl else { throw MirAIeError("Offline guards failed.") }
            if let image = view.bitmapImageRepForCachingDisplay(in: view.bounds) {
                view.cacheDisplay(in: view.bounds, to: image)
                if let data = image.representation(using: .png, properties: [:]) {
                    try data.write(to: directory.appendingPathComponent("offline.png"), options: .withoutOverwriting)
                }
            }
            popover.performClose(nil)
            guard !popover.isShown else { throw MirAIeError("The popover did not close.") }
            statusItem.button?.performClick(nil)
            guard popover.isShown else { throw MirAIeError("The popover did not reopen.") }
            let report: [String: Any] = ["passed": true, "nativePopover": true,
                "temperatureConfirmed": true, "offlinePowerHidden": true,
                "popoverSize": [Int(view.bounds.width), Int(view.bounds.height)],
                "usesRealAccount": false, "liquidGlassAvailable": ProcessInfo.processInfo.operatingSystemVersion.majorVersion >= 26]
            let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: directory.appendingPathComponent("result.json"), options: .withoutOverwriting)
        } catch {
            let reason = (error as? MirAIeError)?.message ?? "Native UI smoke test failed."
            let report: [String: Any] = ["passed": false, "reason": reason,
                "popoverShown": popover.isShown, "devices": controller.devices.count,
                "buttonVisible": statusItem.button?.window?.isVisible ?? false,
                "buttonBounds": NSStringFromRect(statusItem.button?.bounds ?? .zero),
                "popoverWindowVisible": popover.contentViewController?.view.window?.isVisible ?? false]
            let data = (try? JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted])) ?? Data()
            try? data.write(to: URL(fileURLWithPath: output).appendingPathComponent("result.json"), options: .withoutOverwriting)
        }
        NSApp.terminate(nil)
    }
}

MainActor.assumeIsolated {
    let application = NSApplication.shared
    let delegate = AppDelegate()
    application.delegate = delegate
    withExtendedLifetime(delegate) { application.run() }
}
