import 'package:flet/flet.dart';

import 'tgdl_native.dart';

class Extension extends FletExtension {
  @override
  FletService? createService(Control control) {
    switch (control.type) {
      case "TgdlNative":
        return TgdlNativeService(control: control);
      default:
        return null;
    }
  }
}
