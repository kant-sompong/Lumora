import 'package:flutter/widgets.dart';
import 'package:provider/provider.dart';

import '../../../data/settings_store.dart';

/// The interface scale the media pages lay out with.
///
/// [AppSettings.uiScale] is already clamped to 0.8-1.3 by the setting. Text is
/// scaled globally by `MaterialApp.builder`, so this factor is only applied to
/// non-text sizes. Without an [AppSettings] provider (some tests), the neutral
/// 1.0 keeps the previous layout.
double mediaUiScale(BuildContext context) {
  try {
    return AppSettings.clampUiScale(context.watch<AppSettings>().uiScale);
  } on ProviderNotFoundException {
    return 1.0;
  }
}
