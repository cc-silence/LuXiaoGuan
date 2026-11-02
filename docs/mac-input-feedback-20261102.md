# MAC input validation feedback

The AP editor now supplies its existing wireless MAC validator to the reusable value-list control. Nonempty rows validate on edits, removal and submit. Invalid rows use the shared inputError border; the dialog uses the existing settingsValidationError label. Correcting/removing the last invalid row clears this feedback. Both save modes use the same validation path before any service operation. Untouched configuration and the existing colon-separated MAC rules are preserved; optional empty rows remain allowed. Clearing a previously populated list still requires at least one address.

No network calls, timers, platform branches, persistent caches or new styles were added. The shared list remains limited to 512 rows, and removed widgets retain the existing deferred deletion. DNS lists do not opt into the MAC validator.

Windows validation: 28 focused tests passed covering MAC feedback, both save buttons, corrections, deletion, optional/required empty lists, multiple addresses per row, wireless controls and UI, password behavior and unavailable roaming controls. Actual dark/light screenshots were inspected in build/mac-feedback-review. Syntax compilation and UTF-8 checks passed.

Debian 12 desktop execution was not available. Pending check: open an existing AP editor on Debian 12 x86-64, enter an invalid MAC in each theme, click each save button, verify red row border and red explanatory text with no save, then correct/remove the invalid row and confirm the feedback clears. This change uses the same Qt/Python path on both systems; Windows checks do not establish Debian runtime success. No router settings were written during validation.
