import SwiftUI
import ServiceManagement
import MirAIeKit

@MainActor struct AccountView: View {
    @ObservedObject var controller: ACController
    @ViewState private var username = ""
    @ViewState private var password = ""
    @ViewState private var loginEnabled = SMAppService.mainApp.status == .enabled
    @ViewState private var loginError = ""

    var body: some View {
        VStack(spacing: 0) {
            Form {
                Section {
                    if !controller.username.isEmpty {
                        LabeledContent("Account", value: controller.username)
                        LabeledContent("Air conditioners", value: String(controller.devices.count))
                        Button("Sign Out", role: .destructive) {
                            Task { await controller.signOut(); password = "" }
                        }.disabled(controller.isBusy)
                    } else {
                        TextField("Email or mobile number", text: $username)
                            .textContentType(.username).autocorrectionDisabled()
                            .accessibilityIdentifier("account-username")
                        SecureField("Password", text: $password)
                            .textContentType(.password).accessibilityIdentifier("account-password")
                            .onSubmit { signIn() }
                        HStack {
                            Text("Saved securely in macOS Keychain.").font(.caption).foregroundStyle(.secondary)
                            Spacer()
                            if controller.isBusy { ProgressView().controlSize(.small) }
                            Button("Sign In", action: signIn)
                                .modifier(GlassButton(prominent: true))
                                .disabled(controller.isBusy || username.trimmingCharacters(in: .whitespaces).isEmpty || password.isEmpty)
                                .accessibilityIdentifier("account-sign-in")
                        }
                    }
                } header: { Text("MirAIe account") }
                if !controller.error.isEmpty {
                    Section { Text(controller.error).foregroundStyle(.secondary) }
                }
                Section {
                    Toggle("Open at login", isOn: $loginEnabled)
                        .onChange(of: loginEnabled) { _, enabled in updateLogin(enabled) }
                    Text("MirAIe Control stays in your menu bar. Cloud controls require Internet access.")
                        .font(.caption).foregroundStyle(.secondary)
                    if !loginError.isEmpty { Text(loginError).font(.caption).foregroundStyle(.secondary) }
                } header: { Text("Menu bar") }
            }.formStyle(.grouped)
            HStack {
                Image(systemName: "snowflake")
                Text("MirAIe Control").fontWeight(.medium)
                Spacer()
                Text("Community edition").foregroundStyle(.secondary)
            }.font(.caption).padding(18)
        }.frame(width: 460, height: 450)
    }

    private func signIn() {
        guard !controller.isBusy, !username.isEmpty, !password.isEmpty else { return }
        let enteredUsername = username
        let enteredPassword = password
        Task {
            await controller.signIn(username: enteredUsername, password: enteredPassword)
            password = ""
        }
    }
    private func updateLogin(_ enabled: Bool) {
        do {
            if enabled { try SMAppService.mainApp.register() }
            else { try SMAppService.mainApp.unregister() }
            loginError = ""
        } catch {
            loginError = "macOS could not change this setting. Check System Settings > General > Login Items."
            loginEnabled = SMAppService.mainApp.status == .enabled
        }
    }
}
