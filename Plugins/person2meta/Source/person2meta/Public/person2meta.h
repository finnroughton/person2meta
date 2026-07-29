#pragma once

#include "CoreMinimal.h"
#include "Modules/ModuleManager.h"

/**
 * Module for the Person2Meta plugin.
 * Currently an empty shell — no functionality wired up yet.
 * This exists so the plugin loads cleanly in the editor and can be
 * expanded incrementally (upload UI, KeenTools call, MetaHuman Identity setup).
 */
class FPerson2MetaModule : public IModuleInterface
{
public:
	/** IModuleInterface implementation */
	virtual void StartupModule() override;
	virtual void ShutdownModule() override;
};
