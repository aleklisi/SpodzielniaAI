/** Polish messages for the backend's machine-readable error codes. */

export const UPLOAD_ERRORS: Record<string, string> = {
  executable: "Pliki wykonywalne są niedozwolone.",
  extension_mismatch: "Rozszerzenie pliku nie zgadza się z jego zawartością.",
  content_type_mismatch: "Typ pliku nie zgadza się z jego zawartością.",
  type_not_allowed: "Ten typ pliku nie jest obsługiwany.",
  macros_not_allowed: "Dokumenty z makrami są niedozwolone.",
  pdf_encrypted: "Zaszyfrowanych plików PDF nie można przetworzyć.",
  pdf_corrupt: "Plik PDF jest uszkodzony.",
  image_corrupt: "Zdjęcie jest uszkodzone.",
  image_too_many_pixels: "Zdjęcie ma zbyt dużą rozdzielczość.",
  media_corrupt: "Nagranie jest uszkodzone.",
  too_large: "Plik jest za duży.",
  infected: "Plik zawiera złośliwe oprogramowanie i został odrzucony.",
  archive_nested: "Archiwa zagnieżdżone są niedozwolone.",
  note_not_found: "Notatka nie istnieje.",
};

export const SSRF_REASONS: Record<string, string> = {
  private_address: "Adres wskazuje na sieć prywatną lub wewnętrzną.",
  internal_hostname: "Adres wskazuje na serwer wewnętrzny.",
  scheme_not_allowed: "Dozwolone są tylko adresy https://.",
  port_not_allowed: "Ten port jest niedozwolony.",
  credentials_in_url: "Adres nie może zawierać loginu ani hasła.",
  ip_encoding: "Nietypowy zapis adresu IP jest niedozwolony.",
  invalid_hostname: "Nieprawidłowa nazwa serwera.",
  invalid_url: "Nieprawidłowy adres.",
  url_too_long: "Adres jest za długi.",
};

export const DERIVATIVE_KIND: Record<string, string> = {
  TRANSCRIPT: "Transkrypcja",
  IMAGE_DESCRIPTION: "Opis zdjęcia",
  PARSED_DOC: "Treść dokumentu",
  FETCHED_LINK: "Strona internetowa",
  ANNOTATIONS: "Oznaczenia zdjęcia",
  RECOGNITION: "Rozpoznawanie",
};

export const DERIVATIVE_STATUS: Record<string, string> = {
  PENDING: "W kolejce",
  PROCESSING: "Przetwarzanie…",
  DONE: "Gotowe",
  FAILED: "Błąd przetwarzania",
  QUARANTINED: "Kwarantanna",
};

export function uploadError(code: string | undefined, status: number): string {
  if (code && UPLOAD_ERRORS[code]) return UPLOAD_ERRORS[code];
  if (status === 415) return UPLOAD_ERRORS.type_not_allowed;
  if (status === 413) return UPLOAD_ERRORS.too_large;
  return "Nie udało się wgrać pliku.";
}

export function ssrfError(reason: string | undefined): string {
  return (reason && SSRF_REASONS[reason]) || "Tego adresu nie można dodać.";
}
