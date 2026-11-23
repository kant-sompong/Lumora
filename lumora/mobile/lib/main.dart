import 'package:flutter/material.dart';

import 'app.dart';
import 'data/settings_store.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final settings = await AppSettings.load();
  runApp(LumoraApp(settings: settings));
}
