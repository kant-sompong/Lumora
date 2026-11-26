import 'package:flutter_test/flutter_test.dart';
import 'package:lumora_mobile/ui/settings/provider_order.dart';

void main() {
  group('moveEntry', () {
    test('moves a row down by the reported insertion index', () {
      expect(
        moveEntry(<String>['a', 'b', 'c'], 0, 2),
        <String>['b', 'a', 'c'],
      );
    });

    test('moves a row to the end', () {
      expect(
        moveEntry(<String>['a', 'b', 'c'], 0, 3),
        <String>['b', 'c', 'a'],
      );
    });

    test('moves a row up', () {
      expect(
        moveEntry(<String>['a', 'b', 'c'], 2, 0),
        <String>['c', 'a', 'b'],
      );
    });

    test('ignores an out-of-range index', () {
      expect(
        moveEntry(<String>['a', 'b'], 5, 0),
        <String>['a', 'b'],
      );
    });
  });

  group('reorderEnabled', () {
    test('returns the enabled ids in their new order', () {
      final order = <String>['a', 'b', 'c', 'd'];
      final enabled = <String>{'a', 'b', 'd'};
      expect(
        reorderEnabled(order, enabled, 0, 2),
        <String>['b', 'a', 'd'],
      );
    });

    test('never adds a disabled row', () {
      final order = <String>['a', 'b', 'c'];
      final enabled = <String>{'a', 'c'};
      expect(
        reorderEnabled(order, enabled, 0, 2),
        <String>['a', 'c'],
      );
    });
  });
}
