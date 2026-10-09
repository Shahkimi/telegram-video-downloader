import 'dart:async';

import 'package:flet/flet.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

/// Bridges the Python `TgdlNative` service to the Kotlin plugin.
///
/// Python -> Android: every method listed in [_methods] is forwarded as is.
/// Android -> Python: the plugin calls "shared" when something lands in the inbox; this raises the `shared` event.
/// The event carries no text. Python fetches it with `take_pending`, so a share that arrives while nobody
/// listens is never lost.
class TgdlNativeService extends FletService {
  TgdlNativeService({required super.control});

  static const MethodChannel _channel =
      MethodChannel('io.github.shahkimi.tgdl/native');

  static const Set<String> _methods = {
    'take_pending',
    'start_keep_alive',
    'update_keep_alive',
    'stop_keep_alive',
    'public_downloads_dir',
    'scan_file',
    'scan_files',
    'has_all_files_access',
    'request_all_files_access',
    'sdk_int',
    'request_permission',
    'request_ignore_battery_optimizations',
  };

  @override
  void init() {
    super.init();
    debugPrint("TgdlNative(${control.id}).init()");
    control.addInvokeMethodListener(_invokeMethod);
    _channel.setMethodCallHandler(_fromAndroid);
    // The app may have been opened by a share. Let Python know there is something to collect.
    _announce();
  }

  Future<dynamic> _fromAndroid(MethodCall call) async {
    if (call.method == 'shared') {
      _announce();
    }
    return null;
  }

  void _announce() {
    control.triggerEvent("shared");
  }

  Future<dynamic> _invokeMethod(String name, dynamic args) async {
    if (!_methods.contains(name)) {
      throw Exception("Unknown TgdlNative method: $name");
    }
    return await _channel.invokeMethod<dynamic>(name, args);
  }

  @override
  void dispose() {
    debugPrint("TgdlNative(${control.id}).dispose()");
    control.removeInvokeMethodListener(_invokeMethod);
    _channel.setMethodCallHandler(null);
    super.dispose();
  }
}
