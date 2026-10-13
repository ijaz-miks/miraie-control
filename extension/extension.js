import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import {Backend} from './lib/backend.js';
import {Indicator} from './ui/indicator.js';

export default class MiraieExtension extends Extension {
    enable() {
        try {
            this.settings = this.getSettings();
            this.backend = new Backend();
            this.indicator = new Indicator(this.backend, this.settings);
            Main.panel.addToStatusArea(this.uuid, this.indicator);
            this.backend.start();
        } catch (error) { this.disable(); throw error; }
    }
    disable() {
        this.backend?.stop();
        this.indicator?.destroy();
        this.indicator = null;
        this.backend = null;
        this.settings = null;
    }
}
