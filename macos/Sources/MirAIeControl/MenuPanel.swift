import SwiftUI
import MirAIeKit

// Explicitly select the property wrapper, including on SDKs with a State macro.
typealias ViewState<Value> = SwiftUI.State<Value>

struct GlassButton: ViewModifier {
    var prominent = false
    @ViewBuilder func body(content: Content) -> some View {
        if #available(macOS 26.0, *) {
            if prominent { content.buttonStyle(.glassProminent) }
            else { content.buttonStyle(.glass) }
        } else {
            if prominent { content.buttonStyle(.borderedProminent) }
            else { content.buttonStyle(.bordered) }
        }
    }
}

@MainActor struct MenuPanel: View {
    @ObservedObject var controller: ACController
    let openAccount: () -> Void
    @ViewState private var advanced = false

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider().padding(.horizontal, 18)
            ScrollView {
                VStack(spacing: 18) {
                    if let device = controller.selectedDevice {
                        devicePicker(device)
                        temperature(device)
                        controls(device)
                    } else { emptyState }
                    if !controller.error.isEmpty {
                        Label(controller.error, systemImage: "exclamationmark.circle")
                            .font(.callout).foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .accessibilityIdentifier("controller-error")
                    }
                }.padding(20)
            }
            Divider().padding(.horizontal, 18)
            footer
        }
        .frame(width: 360, height: 540)
        .task { await controller.refresh() }
    }

    private var header: some View {
        HStack(spacing: 10) {
            Image(systemName: "snowflake").font(.title3).foregroundStyle(.tint)
            Text("MirAIe Control").font(.headline)
            Spacer()
            if controller.isBusy {
                ProgressView().controlSize(.small)
                    .help(controller.pendingField.map { "Confirming \($0.title.lowercased())" } ?? "Refreshing status")
            }
        }.padding(18)
    }

    private func devicePicker(_ device: Device) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            if controller.devices.count > 1 {
                Picker("Air conditioner", selection: $controller.selectedID) {
                    ForEach(controller.devices) { item in
                        Text(item.name).tag(item.id)
                    }
                }.pickerStyle(.menu).labelsHidden().controlSize(.large)
                    .accessibilityIdentifier("device-picker")
            } else { Text(device.name).font(.title3.weight(.semibold)) }
            HStack(spacing: 6) {
                Circle().fill(device.online ? Color.green : Color.secondary).frame(width: 6, height: 6)
                Text(device.online ? "Online" : "Offline")
                if !device.room.isEmpty { Text("·"); Text(device.room).lineLimit(1) }
                Spacer()
            }.font(.caption).foregroundStyle(.secondary)
                .accessibilityElement(children: .combine)
        }.frame(maxWidth: .infinity, alignment: .leading)
    }

    private func temperature(_ device: Device) -> some View {
        VStack(spacing: 10) {
            Text(device.online ? "Target temperature" : "Last known target")
                .font(.subheadline).foregroundStyle(.secondary)
            HStack(spacing: 22) {
                Button { step(-1, device: device) } label: {
                    Image(systemName: "minus").frame(width: 22, height: 28)
                }.modifier(GlassButton())
                    .disabled(!canStep(-1, device: device))
                    .accessibilityLabel("Decrease target temperature")
                Text(device.temperature.map { "\(format($0))°" } ?? "Unavailable")
                    .font(.system(size: device.temperature == nil ? 24 : 52, weight: .medium, design: .rounded))
                    .monospacedDigit().frame(minWidth: 96)
                    .accessibilityIdentifier("target-temperature")
                Button { step(1, device: device) } label: {
                    Image(systemName: "plus").frame(width: 22, height: 28)
                }.modifier(GlassButton())
                    .disabled(!canStep(1, device: device))
                    .accessibilityLabel("Increase target temperature")
            }
            if let room = device.roomTemperature {
                Label("\(device.online ? "Room" : "Last known room") \(format(room))°C", systemImage: "thermometer.medium")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            if let field = controller.pendingField {
                Text("Confirming \(field.title.lowercased())…")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }.frame(maxWidth: .infinity).padding(.vertical, 4)
    }

    private func controls(_ device: Device) -> some View {
        VStack(spacing: 14) {
            HStack {
                Label("Power", systemImage: "power")
                Spacer()
                if let power = device.power {
                    Toggle("Power", isOn: Binding(get: { power }, set: { send(.power, .bool($0)) }))
                        .labelsHidden().toggleStyle(.switch).disabled(!controller.canControl)
                        .accessibilityIdentifier("power-toggle")
                } else { Text(device.online ? "Unavailable" : "Offline").foregroundStyle(.secondary) }
            }
            choice(.mode, symbol: "snowflake", device: device)
            choice(.fan, symbol: "fan", device: device)
            if hasAdvanced(device) {
                Divider()
                DisclosureGroup("More controls", isExpanded: $advanced) {
                    VStack(spacing: 14) {
                        choice(.verticalSwing, symbol: "arrow.up.arrow.down", device: device)
                        choice(.horizontalSwing, symbol: "arrow.left.arrow.right", device: device)
                        choice(.capacity, symbol: "gauge.with.dots.needle.67percent", device: device)
                        switchRow(.display, symbol: "lightbulb", device: device)
                        switchRow(.nanoe, symbol: "sparkles", device: device)
                    }.padding(.top, 12)
                }.font(.subheadline)
            }
        }.font(.body)
    }

    @ViewBuilder private func choice(_ field: ControlField, symbol: String, device: Device) -> some View {
        let values = device.options[field, default: []]
        if !values.isEmpty {
            HStack {
                Label(field.title, systemImage: symbol)
                Spacer()
                Picker(field.title, selection: Binding(get: { device.value(field)?.text ?? "" },
                    set: { send(field, .string($0)) })) {
                    if device.value(field)?.text == nil { Text("Unavailable").tag("") }
                    ForEach(values, id: \.self) { value in Text(label(value, field: field)).tag(value) }
                }.labelsHidden().pickerStyle(.menu).fixedSize()
                    .disabled(!controller.canControl || device.value(field) == nil)
                    .accessibilityIdentifier(field.rawValue + "-picker")
            }
        }
    }

    @ViewBuilder private func switchRow(_ field: ControlField, symbol: String, device: Device) -> some View {
        if device.features.contains(field), let flag = device.value(field)?.flag {
            Toggle(isOn: Binding(get: { flag }, set: { send(field, .bool($0)) })) {
                Label(field.title, systemImage: symbol)
            }.toggleStyle(.switch).disabled(!controller.canControl)
        }
    }

    private var emptyState: some View {
        VStack(spacing: 14) {
            Image(systemName: "air.conditioner.horizontal").font(.system(size: 38)).foregroundStyle(.secondary)
            Text(controller.connection == .ready ? "No air conditioners found" : "Your home, a little cooler")
                .font(.title3.weight(.semibold))
            Text(controller.connection == .ready ? "Pair your AC in the MirAIe mobile app, then reconnect your account here." :
                "Sign in to control your MirAIe air conditioners from the menu bar.")
                .font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center)
            Button("Account Settings", action: openAccount).modifier(GlassButton(prominent: true))
        }.frame(maxWidth: .infinity).padding(.vertical, 40)
    }

    private var footer: some View {
        HStack {
            Button { Task { await controller.refresh() } } label: { Label("Refresh", systemImage: "arrow.clockwise") }
                .buttonStyle(.borderless).disabled(controller.isBusy || controller.username.isEmpty)
                .help("Get current device status")
            Spacer()
            Button(action: openAccount) { Image(systemName: "gearshape") }
                .buttonStyle(.borderless).accessibilityLabel("Account Settings")
            Menu {
                Button("Account Settings…", action: openAccount)
                Divider()
                Button("Quit MirAIe Control") { NSApplication.shared.terminate(nil) }
            } label: { Image(systemName: "ellipsis.circle") }
                .menuStyle(.borderlessButton).fixedSize().accessibilityLabel("More options")
        }.font(.subheadline).padding(16)
    }

    private func send(_ field: ControlField, _ value: JSONValue) {
        Task { await controller.command(field, value: value) }
    }
    private func canStep(_ direction: Double, device: Device) -> Bool {
        guard controller.canControl, let value = device.temperature else { return false }
        return device.temperatureRange.contains(value + direction * device.temperatureRange.step)
    }
    private func step(_ direction: Double, device: Device) {
        guard canStep(direction, device: device), let value = device.temperature else { return }
        send(.temperature, .number(value + direction * device.temperatureRange.step))
    }
    private func hasAdvanced(_ device: Device) -> Bool {
        [.verticalSwing, .horizontalSwing, .capacity].contains { !device.options[$0, default: []].isEmpty } ||
            device.features.contains(.nanoe) || device.features.contains(.display)
    }
    private func format(_ value: Double) -> String { JSONValue.number(value).text ?? "" }
    private func label(_ value: String, field: ControlField) -> String {
        if field == .capacity { return value == "0" ? "Auto" : value + "%" }
        if [.verticalSwing, .horizontalSwing].contains(field) { return value == "0" ? "Auto" : "Position " + value }
        return value == "fan" ? "Fan only" : value.capitalized
    }
}
