#include "person2meta.h"

#define LOCTEXT_NAMESPACE "FPerson2MetaModule"

void FPerson2MetaModule::StartupModule()
{
	// This gets called after the module is loaded into memory.
	// Nothing wired up yet — this log line is just here so it's obvious
	// in the Output Log that the plugin loaded successfully.
	UE_LOG(LogTemp, Log, TEXT("Person2Meta: module started."));
}

void FPerson2MetaModule::ShutdownModule()
{
	// This gets called when the module is unloaded (e.g. on editor shutdown,
	// or when hot-reloading during development).
	UE_LOG(LogTemp, Log, TEXT("Person2Meta: module shut down."));
}

#undef LOCTEXT_NAMESPACE

IMPLEMENT_MODULE(FPerson2MetaModule, person2meta)
