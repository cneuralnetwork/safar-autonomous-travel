import { Platform } from 'react-native';
import type { Itinerary } from '@/types';
import { buildItineraryIcs, itineraryIcsFilename } from '@/utils/ics';

export async function exportItineraryCalendar(
  itinerary: Itinerary,
  tripTitle: string,
) {
  const contents = buildItineraryIcs(itinerary, tripTitle);
  const filename = itineraryIcsFilename(tripTitle);

  if (Platform.OS === 'web') {
    const blob = new Blob([contents], {
      type: 'text/calendar;charset=utf-8',
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    anchor.style.display = 'none';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
    return filename;
  }

  const FileSystem = await import('expo-file-system/legacy');

  if (Platform.OS === 'android') {
    const initialDirectory =
      FileSystem.StorageAccessFramework.getUriForDirectoryInRoot('Download');
    const permission =
      await FileSystem.StorageAccessFramework.requestDirectoryPermissionsAsync(
        initialDirectory,
      );
    if (!permission.granted) {
      throw new Error(
        'Choose a folder to save the calendar file. Nothing was shared.',
      );
    }
    const displayName = filename.replace(/\.ics$/i, '');
    const uri = await FileSystem.StorageAccessFramework.createFileAsync(
      permission.directoryUri,
      displayName,
      'text/calendar',
    );
    await FileSystem.StorageAccessFramework.writeAsStringAsync(uri, contents, {
      encoding: FileSystem.EncodingType.UTF8,
    });
    return filename;
  }

  const directory = FileSystem.documentDirectory || FileSystem.cacheDirectory;
  if (!directory) {
    throw new Error('A writable folder is not available on this device.');
  }

  const uri = `${directory}${filename}`;
  await FileSystem.writeAsStringAsync(uri, contents, {
    encoding: FileSystem.EncodingType.UTF8,
  });
  return filename;
}
